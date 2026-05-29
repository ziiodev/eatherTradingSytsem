"""Coverage for ``GET /api/projects/{id}/sleep-runs/{run_id}/report``.

Added by sleep-learning-loop Phase 9. Read-only — writes happen
exclusively inside the orchestrator transaction.

Invariants:

* 401 without a session.
* 404 when the caller does not own the project.
* 404 when the sleep_run belongs to a different project.
* 404 when no report has been written for the run.
* 404 cross-tenant (existence non-disclosure per multi-tenancy-delta).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _login(client, *, email: str | None = None):
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    get_settings.cache_clear()
    email = email or f"report-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct horse battery staple"
    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user


async def _seed_project(owner):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project

    maker = get_session_maker()
    async with maker() as session:
        project = await seed_project(
            session, owner=owner, name=f"p-{uuid.uuid4().hex[:8]}"
        )
        await session.commit()
        return project


async def _seed_sleep_run(*, project, phase_type: str = "micro") -> uuid.UUID:
    """Create a sleep_run row owned by ``project.user_id``."""
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.repositories import SleepRunRepository

    maker = get_session_maker()
    async with maker() as session:
        run = await SleepRunRepository(session).create(
            project_id=project.id,
            user_id=project.user_id,
            phase_type=phase_type,
        )
        await session.commit()
        return run.id


async def _seed_report(*, user_id: uuid.UUID, sleep_run_id: uuid.UUID) -> None:
    """Insert the 1:1 sleep_reports row for ``sleep_run_id``."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.sleep_report_repository import (
        SleepReportRepository,
    )

    maker = get_session_maker()
    async with maker() as session:
        await SleepReportRepository(session).insert(
            user_id=user_id,
            sleep_run_id=sleep_run_id,
            summary="ok",
            auditor_metrics={"sharpe": 1.2},
            worker_insights={"avg_holding_min": 42},
            improvements_applied=[],
            q_table_before={},
            q_table_after={},
            overall_score=0.75,
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
async def test_get_sleep_report_requires_auth(app_client) -> None:
    resp = await app_client.get(
        f"/api/projects/{uuid.uuid4()}/sleep-runs/{uuid.uuid4()}/report"
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_get_sleep_report_happy_path(app_client) -> None:
    user = await _login(app_client)
    project = await _seed_project(user)
    run_id = await _seed_sleep_run(project=project)
    await _seed_report(user_id=user.id, sleep_run_id=run_id)

    resp = await app_client.get(
        f"/api/projects/{project.id}/sleep-runs/{run_id}/report"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sleep_run_id"] == str(run_id)
    assert body["summary_md"] == "ok"
    assert body["payload"]["overall_score"] == 0.75
    assert body["payload"]["auditor_metrics"]["sharpe"] == 1.2


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------
async def test_get_sleep_report_missing_returns_404(app_client) -> None:
    """Run exists, project owned, but no report row yet."""
    user = await _login(app_client)
    project = await _seed_project(user)
    run_id = await _seed_sleep_run(project=project)

    resp = await app_client.get(
        f"/api/projects/{project.id}/sleep-runs/{run_id}/report"
    )
    assert resp.status_code == 404


async def test_get_sleep_report_wrong_project_is_404(app_client) -> None:
    """Run belongs to project X, caller asks under project Y (both owned)."""
    user = await _login(app_client)
    project_x = await _seed_project(user)
    project_y = await _seed_project(user)
    run_x = await _seed_sleep_run(project=project_x)
    await _seed_report(user_id=user.id, sleep_run_id=run_x)

    resp = await app_client.get(
        f"/api/projects/{project_y.id}/sleep-runs/{run_x}/report"
    )
    assert resp.status_code == 404


async def test_get_sleep_report_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    project_a = await _seed_project(user_a)
    run_a = await _seed_sleep_run(project=project_a)
    await _seed_report(user_id=user_a.id, sleep_run_id=run_a)

    app_client.cookies.clear()
    await _login(app_client, email="b@example.com")
    resp = await app_client.get(
        f"/api/projects/{project_a.id}/sleep-runs/{run_a}/report"
    )
    # MUST be 404, not 403 — existence is NOT disclosed.
    assert resp.status_code == 404
