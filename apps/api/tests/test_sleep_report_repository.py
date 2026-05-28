"""Tests for :class:`SleepReportRepository` — tenancy via sleep_runs → projects."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_with_sleep_runs():
    """Seed user A + project A + sleep_run, user B + project B + sleep_run."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.sleep_run import SleepRun

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(session, email="a@example.com", password="testtesttesttest")
        user_b = await seed_user(session, email="b@example.com", password="testtesttesttest")
        proj_a = await seed_project(session, owner=user_a, name="proj-a")
        proj_b = await seed_project(session, owner=user_b, name="proj-b")
        run_a = SleepRun(
            project_id=proj_a.id,
            user_id=user_a.id,
            phase_type="micro",
            status="running",
        )
        run_b = SleepRun(
            project_id=proj_b.id,
            user_id=user_b.id,
            phase_type="micro",
            status="running",
        )
        session.add_all([run_a, run_b])
        await session.flush()
        await session.commit()
        return user_a.id, user_b.id, run_a.id, run_b.id


# ---------------------------------------------------------------------------
# insert + get_by_run_id (happy path)
# ---------------------------------------------------------------------------


async def test_insert_and_get_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.sleep_report_repository import (
        SleepReportRepository,
    )

    user_a_id, _, run_a_id, _ = await _seed_two_users_with_sleep_runs()

    maker = get_session_maker()
    async with maker() as session:
        repo = SleepReportRepository(session)
        report = await repo.insert(
            user_id=user_a_id,
            sleep_run_id=run_a_id,
            summary="Recovered 12 episodes, promoted Q-Table v3",
            auditor_metrics={"sharpe": 1.4, "pf": 1.7},
            worker_insights={"top_state": "rsi35:up"},
            improvements_applied=["bump_alpha_normal"],
            q_table_before={"sk:a": {"buy": 0.1}},
            q_table_after={"sk:a": {"buy": 0.25}},
            overall_score=0.87,
        )
        await session.commit()
        assert report is not None
        assert report.summary_md == "Recovered 12 episodes, promoted Q-Table v3"
        assert report.payload["auditor_metrics"]["sharpe"] == 1.4
        assert report.payload["worker_insights"]["top_state"] == "rsi35:up"
        assert report.payload["improvements_applied"] == ["bump_alpha_normal"]
        assert report.payload["overall_score"] == 0.87

    async with maker() as session:
        repo = SleepReportRepository(session)
        fetched = await repo.get_by_run_id(
            user_id=user_a_id, sleep_run_id=run_a_id
        )
        assert fetched is not None
        assert fetched.summary_md is not None


# ---------------------------------------------------------------------------
# Cross-tenant probes
# ---------------------------------------------------------------------------


async def test_cross_tenant_get_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.sleep_report_repository import (
        SleepReportRepository,
    )

    user_a_id, user_b_id, run_a_id, _ = await _seed_two_users_with_sleep_runs()

    maker = get_session_maker()
    async with maker() as session:
        repo = SleepReportRepository(session)
        await repo.insert(
            user_id=user_a_id,
            sleep_run_id=run_a_id,
            summary="secret",
            auditor_metrics={},
            worker_insights={},
            improvements_applied=[],
            q_table_before={},
            q_table_after={},
            overall_score=0.0,
        )
        await session.commit()

    async with maker() as session:
        repo = SleepReportRepository(session)
        # User B tries to fetch A's report — must see None.
        fetched = await repo.get_by_run_id(
            user_id=user_b_id, sleep_run_id=run_a_id
        )
        assert fetched is None


async def test_cross_tenant_insert_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.sleep_report_repository import (
        SleepReportRepository,
    )

    user_a_id, user_b_id, run_a_id, _ = await _seed_two_users_with_sleep_runs()

    maker = get_session_maker()
    async with maker() as session:
        repo = SleepReportRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert(
                user_id=user_b_id,
                sleep_run_id=run_a_id,
                summary="x",
                auditor_metrics={},
                worker_insights={},
                improvements_applied=[],
                q_table_before={},
                q_table_after={},
                overall_score=0.0,
            )
