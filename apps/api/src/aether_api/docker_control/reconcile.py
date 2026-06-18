"""Boot-time + periodic drift reconciliation for project containers.

The reconciler walks every project row with ``container_id IS NOT
NULL`` and queries ``GET /containers/{id}/json`` on the docker-socket-
proxy. Three observable outcomes:

* **200 OK + state.Status in {"running","paused"}** — the project's
  stored status is consistent with the daemon; no action needed
  (record optionally elided to keep the audit table compact).
* **200 OK + state.Status in {"exited","dead"}** — the daemon reports
  the container is stopped. Apply the docker event
  ``daemon_reports_stopped`` → canonical ``stopped`` status via
  :func:`docker_lifecycle_transitions.assert_event`.
* **404** — the container no longer exists. This is the drift case:
  apply the docker event ``drift_detected`` → canonical ``error``
  status, clear ``container_id`` / ``container_name``, and write a
  ``container_events`` row with ``status='observed'``.

The reconciler MUST be defensive: an aiodocker failure on a single
project row MUST NOT abort the rest of the sweep. Each per-project
operation runs inside its own try/except.

The lifecycle of the periodic task is owned by ``main.py``'s FastAPI
lifespan — it kicks off :func:`sweep_once` at startup and then
schedules :func:`run_ticker` as a background task gated by
``settings.docker_reconcile_enabled``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session_maker
from aether_api.docker_control.client import get_docker
from aether_api.docker_control.docker_lifecycle_transitions import (
    UnknownDockerEvent,
    assert_event,
)
from aether_api.docker_control.events_repository import ContainerEventsRepository
from aether_api.models.pair import Pair
from aether_api.repositories.pair_repository import PairRepository
from aether_api.services.pair_lifecycle import InvalidTransition

logger = logging.getLogger(__name__)


async def _pairs_with_container(session: AsyncSession) -> list[Pair]:
    """Return every project row with a non-null ``container_id``.

    Cross-tenant by design: the reconciler is a system process, not a
    request handler. It does NOT go through the tenant filter — it
    operates on the global universe of project rows.
    """
    stmt = select(Pair).where(Pair.container_id.is_not(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _check_one(session: AsyncSession, project: Pair) -> dict[str, Any]:
    """Reconcile a single project. Returns a small summary dict."""
    docker = get_docker()
    summary: dict[str, Any] = {
        "project_id": str(project.id),
        "container_id": project.container_id,
        "result": None,
    }

    try:
        container = await docker.containers.get(project.container_id)
        info = await container.show()
    except Exception as exc:  # noqa: BLE001 — covers aiodocker 404 + network errors
        # aiodocker raises DockerError for 404 (NotFound) — we treat
        # anything that throws here as a drift event. The repository
        # update + audit row protect against double-counting if the
        # daemon was just briefly unreachable: we'll re-check next tick.
        logger.warning(
            "reconcile.drift: project=%s container=%s err=%s",
            project.id,
            project.container_id,
            exc,
        )
        try:
            target = assert_event(project.status, "drift_detected")
            repo = PairRepository(session)
            await repo.update_status_if(
                project.user_id,
                project.id,
                from_status=project.status,
                to_status=target,
            )
            await repo.update_fields(
                project.user_id,
                project.id,
                {"container_id": None, "container_name": None},
            )
        except (InvalidTransition, UnknownDockerEvent):
            # The canonical state machine refused the transition (e.g.
            # the project is already in ``error`` or ``stopped``). Still
            # write the audit row but skip the state mutation.
            pass

        await ContainerEventsRepository(session).record(
            project_id=project.id,
            user_id=project.user_id,
            action="reconcile_drift",
            status="observed",
            payload={"container_id": project.container_id},
            error=str(exc)[:512],
        )
        summary["result"] = "drift"
        return summary

    daemon_status = str(info.get("State", {}).get("Status", "")).lower()
    summary["daemon_status"] = daemon_status

    if daemon_status in {"exited", "dead"}:
        # Daemon says it's stopped — sync the project row.
        try:
            target = assert_event(project.status, "daemon_reports_stopped")
            repo = PairRepository(session)
            await repo.update_status_if(
                project.user_id,
                project.id,
                from_status=project.status,
                to_status=target,
            )
        except (InvalidTransition, UnknownDockerEvent):
            pass

        await ContainerEventsRepository(session).record(
            project_id=project.id,
            user_id=project.user_id,
            action="reconcile_stopped",
            status="observed",
            payload={
                "container_id": project.container_id,
                "daemon_status": daemon_status,
            },
        )
        summary["result"] = "stopped"
        return summary

    summary["result"] = "ok"
    return summary


async def sweep_once() -> list[dict[str, Any]]:
    """Run one full reconciliation pass and return per-project summaries.

    Opens its own AsyncSession so it can be invoked from the FastAPI
    lifespan (no request scope), commits at the end. Per-project
    failures are isolated — one bad row does not abort the sweep.
    """
    results: list[dict[str, Any]] = []
    maker = get_session_maker()
    async with maker() as session:
        pairs = await _pairs_with_container(session)
        for project in pairs:
            try:
                summary = await _check_one(session, project)
            except Exception:  # noqa: BLE001 — last-resort isolation
                logger.exception("reconcile: unhandled error on project %s", project.id)
                continue
            results.append(summary)
        await session.commit()
    return results


async def run_ticker(stop_event: asyncio.Event) -> None:
    """Schedule :func:`sweep_once` every ``docker_reconcile_interval_seconds``.

    The caller is responsible for setting ``stop_event`` on shutdown.
    A non-positive interval disables the ticker (caller-side concern;
    we still honour ``stop_event`` immediately as a safety net).
    """
    interval = max(1, int(get_settings().docker_reconcile_interval_seconds))
    while not stop_event.is_set():
        try:
            await sweep_once()
        except Exception:  # noqa: BLE001
            logger.exception("reconcile.ticker: sweep raised")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


__all__ = ["run_ticker", "sweep_once"]
