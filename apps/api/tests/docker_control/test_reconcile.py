"""Drift-detection tests for :func:`docker_control.reconcile.sweep_once`.

The reconciler's three branches are exercised by stubbing the aiodocker
client (no live Docker daemon). For each branch we check:

1. The project row's ``status`` and ``container_id`` after the sweep.
2. The ``container_events`` audit row(s) written.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from aether_api.docker_control import client as docker_client
from aether_api.docker_control import reconcile
from aether_api.models.container_event import ContainerEvent
from aether_api.models.pair import Pair
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# aiodocker stubs
# ---------------------------------------------------------------------------
class _FakeContainer:
    """Mimic the subset of aiodocker.Container that reconcile uses."""

    def __init__(self, state: str | None) -> None:
        self._state = state

    async def show(self) -> dict[str, Any]:
        if self._state is None:
            raise RuntimeError("404 from proxy — container not found")
        return {"State": {"Status": self._state}}


class _FakeContainers:
    def __init__(self, state_by_id: dict[str, str | None]) -> None:
        self._state_by_id = state_by_id

    async def get(self, container_id: str) -> _FakeContainer:
        if container_id not in self._state_by_id:
            raise RuntimeError("404 from proxy")
        return _FakeContainer(self._state_by_id[container_id])


class _FakeDocker:
    def __init__(self, state_by_id: dict[str, str | None]) -> None:
        self.containers = _FakeContainers(state_by_id)


@pytest.fixture
def patch_docker(monkeypatch):
    """Patch :func:`docker_control.client.get_docker` to return a fake."""

    def _install(state_by_id: dict[str, str | None]) -> None:
        fake = _FakeDocker(state_by_id)
        monkeypatch.setattr(docker_client, "get_docker", lambda: fake)
        monkeypatch.setattr(reconcile, "get_docker", lambda: fake)

    return _install


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed_project_with_container(
    *, status: str, container_id: str
) -> uuid.UUID:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session,
            email=f"recon-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        project = await seed_project(session, owner=user)
        project.status = status
        project.container_id = container_id
        project.container_name = f"aether-{container_id[:8]}"
        await session.commit()
        return project.id


async def _project_row(project_id: uuid.UUID) -> Pair:
    from aether_api.db.session import get_session_maker

    maker = get_session_maker()
    async with maker() as session:
        result = await session.execute(
            select(Pair).where(Pair.id == project_id)
        )
        return result.scalar_one()


async def _events_for(project_id: uuid.UUID) -> list[ContainerEvent]:
    from aether_api.db.session import get_session_maker

    maker = get_session_maker()
    async with maker() as session:
        result = await session.execute(
            select(ContainerEvent).where(ContainerEvent.pair_id == project_id)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Branch: daemon says running → no transition.
# ---------------------------------------------------------------------------
async def test_sweep_no_op_when_daemon_reports_running(patch_docker, migrated_db):
    container_id = "cid-" + uuid.uuid4().hex[:16]
    project_id = await _seed_project_with_container(
        status="active", container_id=container_id
    )
    patch_docker({container_id: "running"})

    summaries = await reconcile.sweep_once()
    assert any(s["project_id"] == str(project_id) and s["result"] == "ok" for s in summaries)

    row = await _project_row(project_id)
    assert row.status == "active"
    assert row.container_id == container_id


# ---------------------------------------------------------------------------
# Branch: daemon says exited → daemon_reports_stopped event → stopped.
# ---------------------------------------------------------------------------
async def test_sweep_marks_stopped_when_daemon_reports_exited(
    patch_docker, migrated_db
):
    container_id = "cid-" + uuid.uuid4().hex[:16]
    project_id = await _seed_project_with_container(
        status="active", container_id=container_id
    )
    patch_docker({container_id: "exited"})

    await reconcile.sweep_once()

    row = await _project_row(project_id)
    assert row.status == "stopped"
    events = await _events_for(project_id)
    assert any(e.action == "reconcile_stopped" for e in events)


# ---------------------------------------------------------------------------
# Branch: container missing → drift_detected → error + cleared container_id.
# ---------------------------------------------------------------------------
async def test_sweep_marks_error_and_clears_id_on_drift(patch_docker, migrated_db):
    container_id = "cid-" + uuid.uuid4().hex[:16]
    project_id = await _seed_project_with_container(
        status="active", container_id=container_id
    )
    # Empty state map → ``get`` raises → reconciler treats as drift.
    patch_docker({})

    await reconcile.sweep_once()

    row = await _project_row(project_id)
    assert row.status == "error"
    assert row.container_id is None
    events = await _events_for(project_id)
    assert any(e.action == "reconcile_drift" for e in events)
