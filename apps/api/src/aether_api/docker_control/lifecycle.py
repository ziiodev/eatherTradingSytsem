"""Per-project Docker lifecycle operations.

Each public helper in this module:

1. resolves the project row (tenant-scoped via the repository),
2. asserts the canonical state transition for the user-driven action
   (start/pause/stop), or the docker-event transition (build_failed /
   daemon_reports_stopped) for control-plane signals,
3. issues the aiodocker call against the docker-socket-proxy,
4. updates ``projects.{status, container_id, container_name}`` via
   :meth:`ProjectRepository.update_status_if`,
5. writes a ``container_events`` audit row.

Steps 4 + 5 share the same SQLAlchemy session — the caller commits
once the operation has either fully succeeded or fully failed (router
layer owns the transaction boundary).

Everything is async; the aiodocker calls are the only I/O surface, and
they are routed through the proxy (no raw socket).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.docker_control.client import get_docker
from aether_api.docker_control.docker_lifecycle_transitions import (
    UnknownDockerEvent,
    assert_event,
)
from aether_api.docker_control.events_repository import ContainerEventsRepository
from aether_api.docker_control.sanitize import (
    UnsafeValueError,
    sanitize_env_value,
)
from aether_api.models.project import Project
from aether_api.models.user import User
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.services.project_lifecycle import (
    InvalidTransition,
    assert_transition,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class DockerControlError(RuntimeError):
    """Raised when an aiodocker / proxy call fails.

    Carries the operation name + truncated cause on the instance so the
    router can map it to a structured 502 / 503 payload.
    """

    def __init__(self, op: str, cause: str) -> None:
        self.op = op
        self.cause = cause[:512]
        super().__init__(f"docker.{op}: {self.cause}")


class ProjectNotFoundError(LookupError):
    """Raised when the tenant-scoped project lookup misses (router -> 404)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _load_project(
    session: AsyncSession, user: User, project_id: uuid.UUID
) -> Project:
    """Tenant-scoped project lookup. Raises :class:`ProjectNotFoundError`."""
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise ProjectNotFoundError(str(project_id))
    return project


def _expected_container_name(project: Project) -> str:
    """Derive the deterministic ``aether-{short}`` container name.

    Sanitised through the same allowlist as Dockerfile interpolations —
    container names that fail the regex would be rejected by the daemon
    anyway, so we'd rather fail fast at the call site than after the
    HTTP round-trip.
    """
    short = str(project.id).split("-", 1)[0]
    name = f"aether-{short}"
    return sanitize_env_value(name, field="container_name")


def _expected_image_tag(project: Project) -> str:
    """Derive the deterministic image tag for ``project``."""
    short = str(project.id).split("-", 1)[0]
    return sanitize_env_value(f"aether/{short}:latest", field="image_tag")


async def _record(
    session: AsyncSession,
    *,
    project: Project,
    user: User,
    action: str,
    status: str,
    payload: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Wrapper around the events repo so call sites stay one-liners."""
    await ContainerEventsRepository(session).record(
        project_id=project.id,
        user_id=user.id,
        action=action,
        status=status,
        payload=payload,
        error=error,
    )


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------
async def build_image(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
    dockerfile_text: str,
) -> dict[str, Any]:
    """Build the project image via the proxy ``/build`` endpoint.

    Returns a small dict describing the build (image tag, byte count of
    the Dockerfile sent). Writes a ``container_events`` row with action
    ``"build"`` and ``status`` ``"ok"`` / ``"error"``.

    On failure, the project is moved through the ``build_failed`` docker
    event to ``error`` via :func:`assert_event`, and the original
    aiodocker exception is wrapped in :class:`DockerControlError`.

    NOTE: the caller MUST own the transaction boundary — this function
    flushes but does not commit.
    """
    project = await _load_project(session, user, project_id)
    tag = _expected_image_tag(project)

    docker = get_docker()
    try:
        # aiodocker's images.build accepts a fileobj or a tar context.
        # The sanitized Dockerfile is a small text blob, so we send it
        # as the build context. The proxy's BUILD=1 allowlist permits
        # this endpoint; everything else is denied.
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = dockerfile_text.encode("utf-8")
            info = tarfile.TarInfo(name="Dockerfile")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)

        # aiodocker.images.build is an async generator that yields raw
        # build output frames. Drain it to capture the final status.
        log_tail: list[str] = []
        async for chunk in docker.images.build(
            fileobj=buf, encoding="utf-8", tag=tag, rm=True, stream=True
        ):
            if isinstance(chunk, dict):
                stream = chunk.get("stream") or chunk.get("status") or chunk.get("error")
                if stream:
                    log_tail.append(str(stream))
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
            # Keep only the last 50 lines so a runaway build doesn't
            # balloon the audit row.
            if len(log_tail) > 50:
                log_tail = log_tail[-50:]
    except Exception as exc:  # noqa: BLE001 — wrap all aiodocker / runtime errors
        await _record(
            session,
            project=project,
            user=user,
            action="build",
            status="error",
            payload={"image_tag": tag},
            error=str(exc),
        )
        # Resolve the docker event to a canonical status. If the
        # transition is illegal from the current status (e.g. inactive),
        # we still recorded the build failure above — let the canonical
        # state machine raise so the router maps it cleanly.
        try:
            target = assert_event(project.status, "build_failed")
            repo = ProjectRepository(session)
            await repo.update_status_if(
                user.id,
                project.id,
                from_status=project.status,
                to_status=target,
            )
        except (InvalidTransition, UnknownDockerEvent):
            logger.warning(
                "build_failed event for project %s in status %s — no canonical transition",
                project.id,
                project.status,
            )
        raise DockerControlError("build", str(exc)) from exc

    await _record(
        session,
        project=project,
        user=user,
        action="build",
        status="ok",
        payload={"image_tag": tag, "log_tail": log_tail[-10:]},
    )
    return {"image_tag": tag, "log_lines": len(log_tail)}


async def create_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Create the project container and persist ``container_id`` / ``container_name``.

    Does NOT start it — the caller drives start separately via
    :func:`start_container`. Status transitions on create are deferred
    to start (the project stays in its current status here).
    """
    project = await _load_project(session, user, project_id)
    name = _expected_container_name(project)
    tag = _expected_image_tag(project)

    docker = get_docker()
    try:
        config: dict[str, Any] = {
            "Image": tag,
            "Labels": {
                "aether.project_id": str(project.id),
                "aether.symbol": sanitize_env_value(project.symbol, field="symbol"),
            },
            "HostConfig": {
                # No port bindings to the host by default — the MCP port
                # is only reachable on the internal compose network.
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }
        container = await docker.containers.create_or_replace(name=name, config=config)
        container_id = container.id if hasattr(container, "id") else str(container)
    except UnsafeValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _record(
            session,
            project=project,
            user=user,
            action="create",
            status="error",
            payload={"container_name": name, "image_tag": tag},
            error=str(exc),
        )
        raise DockerControlError("create", str(exc)) from exc

    repo = ProjectRepository(session)
    await repo.update_fields(
        user.id,
        project.id,
        {"container_id": container_id, "container_name": name},
    )
    await _record(
        session,
        project=project,
        user=user,
        action="create",
        status="ok",
        payload={"container_id": container_id, "container_name": name, "image_tag": tag},
    )
    return {"container_id": container_id, "container_name": name}


async def start_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Start the project container. Drives ``status`` → ``active``."""
    project = await _load_project(session, user, project_id)
    if project.container_id is None:
        raise DockerControlError("start", "project has no container_id; create first")

    assert_transition(project.status, "active")

    docker = get_docker()
    try:
        container = await docker.containers.get(project.container_id)
        await container.start()
    except Exception as exc:  # noqa: BLE001
        await _record(
            session,
            project=project,
            user=user,
            action="start",
            status="error",
            payload={"container_id": project.container_id},
            error=str(exc),
        )
        raise DockerControlError("start", str(exc)) from exc

    repo = ProjectRepository(session)
    await repo.update_status_if(
        user.id, project.id, from_status=project.status, to_status="active"
    )
    await _record(
        session,
        project=project,
        user=user,
        action="start",
        status="ok",
        payload={"container_id": project.container_id, "to_status": "active"},
    )
    return {"container_id": project.container_id, "status": "active"}


async def pause_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Pause the project container. Drives ``status`` → ``paused``."""
    project = await _load_project(session, user, project_id)
    if project.container_id is None:
        raise DockerControlError("pause", "project has no container_id")

    assert_transition(project.status, "paused")

    docker = get_docker()
    try:
        container = await docker.containers.get(project.container_id)
        await container.pause()
    except Exception as exc:  # noqa: BLE001
        await _record(
            session,
            project=project,
            user=user,
            action="pause",
            status="error",
            payload={"container_id": project.container_id},
            error=str(exc),
        )
        raise DockerControlError("pause", str(exc)) from exc

    repo = ProjectRepository(session)
    await repo.update_status_if(
        user.id, project.id, from_status=project.status, to_status="paused"
    )
    await _record(
        session,
        project=project,
        user=user,
        action="pause",
        status="ok",
        payload={"container_id": project.container_id, "to_status": "paused"},
    )
    return {"container_id": project.container_id, "status": "paused"}


async def stop_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Stop the project container. Drives ``status`` → ``stopped``."""
    project = await _load_project(session, user, project_id)
    if project.container_id is None:
        raise DockerControlError("stop", "project has no container_id")

    assert_transition(project.status, "stopped")

    docker = get_docker()
    try:
        container = await docker.containers.get(project.container_id)
        await container.stop()
    except Exception as exc:  # noqa: BLE001
        await _record(
            session,
            project=project,
            user=user,
            action="stop",
            status="error",
            payload={"container_id": project.container_id},
            error=str(exc),
        )
        raise DockerControlError("stop", str(exc)) from exc

    repo = ProjectRepository(session)
    await repo.update_status_if(
        user.id, project.id, from_status=project.status, to_status="stopped"
    )
    await _record(
        session,
        project=project,
        user=user,
        action="stop",
        status="ok",
        payload={"container_id": project.container_id, "to_status": "stopped"},
    )
    return {"container_id": project.container_id, "status": "stopped"}


async def recreate_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Stop → remove → create → start the project container.

    Convenience for the ``Recrear`` button. Internally calls the
    individual primitives so every step writes its own audit row.
    """
    project = await _load_project(session, user, project_id)
    container_id_before = project.container_id

    if project.container_id is not None and project.status == "active":
        # Best-effort stop; ignore if already stopped.
        try:
            await stop_container(session, user=user, project_id=project_id)
        except DockerControlError:
            logger.info("recreate: stop step was a no-op (already stopped)")
        # Reload to pick up the new status.
        project = await _load_project(session, user, project_id)

    if project.container_id is not None:
        await remove_container(session, user=user, project_id=project_id)

    await create_container(session, user=user, project_id=project_id)
    await start_container(session, user=user, project_id=project_id)

    await _record(
        session,
        project=project,
        user=user,
        action="recreate",
        status="ok",
        payload={"previous_container_id": container_id_before},
    )
    return {"status": "recreated"}


async def remove_container(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Remove the container and clear ``container_id`` / ``container_name``."""
    project = await _load_project(session, user, project_id)
    if project.container_id is None:
        return {"status": "noop"}

    docker = get_docker()
    try:
        container = await docker.containers.get(project.container_id)
        await container.delete(force=True)
    except Exception as exc:  # noqa: BLE001
        await _record(
            session,
            project=project,
            user=user,
            action="remove",
            status="error",
            payload={"container_id": project.container_id},
            error=str(exc),
        )
        raise DockerControlError("remove", str(exc)) from exc

    repo = ProjectRepository(session)
    await repo.update_fields(
        user.id,
        project.id,
        {"container_id": None, "container_name": None},
    )
    await _record(
        session,
        project=project,
        user=user,
        action="remove",
        status="ok",
        payload={"removed_container_id": project.container_id},
    )
    return {"removed_container_id": project.container_id}


async def container_logs(
    session: AsyncSession,
    *,
    user: User,
    project_id: uuid.UUID,
    tail: int = 200,
) -> str:
    """Return the tail of the container stdout/stderr.

    Read-only; does not write an audit row (the dashboard polls this on
    5s tick and we don't want to flood ``container_events``).
    """
    project = await _load_project(session, user, project_id)
    if project.container_id is None:
        return ""

    docker = get_docker()
    try:
        container = await docker.containers.get(project.container_id)
        # aiodocker returns a list of log lines (or bytes per chunk
        # depending on backend); coerce to a single string.
        log_lines = await container.log(stdout=True, stderr=True, tail=tail)
    except Exception as exc:  # noqa: BLE001
        raise DockerControlError("logs", str(exc)) from exc

    if isinstance(log_lines, list):
        return "".join(
            line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
            for line in log_lines
        )
    if isinstance(log_lines, bytes):
        return log_lines.decode("utf-8", errors="replace")
    return str(log_lines)


__all__ = [
    "DockerControlError",
    "ProjectNotFoundError",
    "build_image",
    "container_logs",
    "create_container",
    "pause_container",
    "recreate_container",
    "remove_container",
    "start_container",
    "stop_container",
]
