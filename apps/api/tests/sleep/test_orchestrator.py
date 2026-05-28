"""Integration coverage for the Sleep Phase orchestrator workflow.

The sandbox engine is mocked here: spawning a real subprocess from
inside pytest's event loop is both slow and racy. The
``_FakeEngine`` class implements the same ``run_agent`` signature the
production Engine exposes (per
``apps/api/src/aether_api/sandbox/engine.py``) and returns a hand-rolled
:class:`aether_api.sandbox.engine.EngineResult`.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# Force sandbox feature flag ON for these tests; cache cleared in conftest.
os.environ["AGENT_SANDBOX_ENABLED"] = "true"


@dataclass
class _Result:
    status: str = "success"
    result: Any = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = 0
    denial_reason: str | None = None
    resource_usage: dict[str, Any] | None = None
    duration_seconds: float = 0.01

    def __post_init__(self) -> None:
        if self.resource_usage is None:
            self.resource_usage = {}
        if not hasattr(self, "run_id"):
            object.__setattr__(self, "run_id", uuid.uuid4())


class _FakeEngine:
    """Mock sandbox Engine. ``per_agent_returns`` keyed by agent type."""

    def __init__(self, per_agent_returns: dict[str, _Result]) -> None:
        self.per_agent_returns = per_agent_returns
        self.calls: list[tuple[str, str]] = []  # (agent_type, mode)

    def run_agent(self, *, agent_row, project_row, inputs, dry_run, mode) -> _Result:
        self.calls.append((agent_row.type, mode))
        return self.per_agent_returns[agent_row.type]


async def _seed_user_project_agents(session, *, with_all_agents: bool = True):
    """Insert a user + project + 3 agents wired into the project."""
    from tests._helpers import seed_agent, seed_project, seed_user

    user = await seed_user(
        session, email=f"sleep-{uuid.uuid4().hex[:8]}@example.com",
        password="correct horse battery staple",
    )
    project = await seed_project(session, owner=user)
    # Override base status — we want 'active'.
    project.status = "active"
    project.worker_params = {"sma_window": 30}
    project.investigator_params = {}
    project.auditor_params = {}

    if with_all_agents:
        w = await seed_agent(session, owner=user, name="w", type="worker")
        i = await seed_agent(session, owner=user, name="i", type="investigator")
        a = await seed_agent(session, owner=user, name="a", type="auditor")
        project.worker_agent_id = w.id
        project.investigator_agent_id = i.id
        project.auditor_agent_id = a.id

    await session.flush()
    await session.commit()
    return user, project


async def test_orchestrator_success_creates_pending_config_version(app_client) -> None:
    """Happy path — all three agents reflect, snapshot diff produces a pending row."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase
    from aether_api.sleep.repositories import (
        ConfigVersionRepository,
        SleepReflectionRepository,
        SleepRunRepository,
    )

    # Make sure settings see the env override.
    get_settings.cache_clear()

    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project_agents(session)

    engine = _FakeEngine(
        per_agent_returns={
            "worker": _Result(
                status="success",
                result={
                    "reflection_md": "worker thinks SMA could widen",
                    "suggested_changes": {"worker_params": {"sma_window": 32}},
                },
            ),
            "investigator": _Result(
                status="success",
                result={
                    "reflection_md": "investigator observed nothing",
                    "suggested_changes": {},
                },
            ),
            "auditor": _Result(
                status="success",
                result={
                    "reflection_md": "auditor notes drawdown ok",
                    "suggested_changes": {"notes": "auditor pass"},
                },
            ),
        }
    )

    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project.id,
            user_id=user.id,
            phase_type="micro",
            engine=engine,
        )

    assert result.status == "succeeded"
    assert result.config_version_id is not None
    assert len(engine.calls) == 3
    assert {call[0] for call in engine.calls} == {"worker", "investigator", "auditor"}
    assert all(mode == "reflection" for _, mode in engine.calls)

    async with maker() as session:
        run = await SleepRunRepository(session).get(result.sleep_run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.ended_at is not None

        refls = await SleepReflectionRepository(session).list_for_run(run.id)
        assert {r.agent_type for r in refls} == {"worker", "investigator", "auditor"}

        cvs = await ConfigVersionRepository(session).list_for_run(run.id)
        assert len(cvs) == 1
        cv = cvs[0]
        assert cv.status == "pending"
        # Worker's +2/30 ≈ 6.7% on sma_window is 'bajo'; notes is unknown
        # bracket only on its own — but notes is a known field, so the
        # numeric path drops to 'alto' (string vs string is structural).
        # Worker change is 'bajo' and notes change pushes to 'alto'.
        assert cv.risk_class == "alto"


async def test_orchestrator_partial_when_one_agent_fails(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    get_settings.cache_clear()
    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project_agents(session)

    engine = _FakeEngine(
        per_agent_returns={
            "worker": _Result(
                status="success",
                result={"suggested_changes": {"worker_params": {"sma_window": 31}}},
            ),
            "investigator": _Result(status="error", stderr="kaboom"),
            "auditor": _Result(status="success", result={"suggested_changes": {}}),
        }
    )

    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project.id,
            user_id=user.id,
            phase_type="micro",
            engine=engine,
        )
    assert result.status == "partial"


async def test_orchestrator_skips_when_project_status_not_runnable(
    app_client,
) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    get_settings.cache_clear()
    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project_agents(session)
        project.status = "stopped"
        await session.commit()

    engine = _FakeEngine(per_agent_returns={})
    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project.id,
            user_id=user.id,
            phase_type="micro",
            engine=engine,
        )
    assert result.status == "skipped"
    assert engine.calls == []


async def test_orchestrator_fails_when_sandbox_disabled(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    os.environ["AGENT_SANDBOX_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        maker = get_session_maker()
        async with maker() as session:
            user, project = await _seed_user_project_agents(session)

        engine = _FakeEngine(per_agent_returns={})
        async with maker() as session:
            result = await run_sleep_phase(
                session,
                project_id=project.id,
                user_id=user.id,
                phase_type="micro",
                engine=engine,
            )
        assert result.status == "failed"
        assert result.error == "sandbox-disabled"
        assert engine.calls == []
    finally:
        os.environ["AGENT_SANDBOX_ENABLED"] = "true"
        get_settings.cache_clear()


async def test_orchestrator_fails_when_no_agents_assigned(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    get_settings.cache_clear()
    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project_agents(session, with_all_agents=False)

    engine = _FakeEngine(per_agent_returns={})
    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project.id,
            user_id=user.id,
            phase_type="micro",
            engine=engine,
        )
    assert result.status == "failed"
    assert result.error and "no agents" in result.error
