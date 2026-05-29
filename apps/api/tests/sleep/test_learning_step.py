"""Phase 7 coverage — orchestrator learning sub-steps 5a + 5b + 5c.

Three concerns this file pins down:

1. **Step 5a / 5b — Q-update pass + special tagging**: synthetic episodic
   data feeds :func:`apply_q_update_pass` and the resulting
   :class:`QUpdatePass` envelope is asserted against the hand-computed
   Bellman expectation. Special tagging fires when ``|reward| >=
   threshold`` OR ``meta_data['rule_violation'] is True``.

2. **R1 atomic-tx — forced rollback**: each of the three persistence
   paths (``q_tables`` INSERT / ``sleep_reports`` INSERT /
   ``config_versions`` INSERT) is independently monkey-patched to raise.
   For every path the test asserts ZERO new rows in ALL THREE target
   tables — no partial Q-Table version ever survives a crashed finalize.

3. **Cache invalidation**: the deep-sleep ``profundo`` orchestrator must
   call ``learning_cache.invalidate(user_id, project_id)`` AFTER the
   outer transaction commits, and MUST NOT invalidate it when the
   transaction rolled back. Both branches are asserted.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared seed helpers — keep the test bodies focused on assertions.
# ---------------------------------------------------------------------------


async def _seed_user_project(*, risk_per_trade: Decimal | None = None):
    """Seed a user + an ``active`` project. Returns (user_id, project_id)."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session,
            email=f"phase7-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        project = await seed_project(session, owner=user)
        project.status = "active"
        # Pin orchestrator_params + risk_per_trade so the special-trade
        # threshold is predictable across tests.
        project.orchestrator_params = {"special_trade_threshold": 2.0}
        if risk_per_trade is not None:
            project.risk_per_trade = risk_per_trade
        await session.commit()
        return user.id, project.id


async def _seed_episode(
    session,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    state_key: str,
    action: str,
    reward: Decimal | float,
    next_state_key: str | None = None,
    rule_violation: bool = False,
    created_at: datetime | None = None,
):
    """Append one episodic_memory row. ``created_at`` overrides the default."""
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    repo = EpisodicMemoryRepository(session)
    ep = await repo.insert(
        user_id=user_id,
        project_id=project_id,
        trade_id=None,
        state={"hint": state_key},
        state_key=state_key,
        action=action,
        reward=Decimal(str(reward)),
        result="win" if float(reward) >= 0 else "loss",
        worker_reasoning="phase7-test",
        q_value_before=Decimal("0.0"),
        q_value_after=Decimal("0.0"),
        is_special=False,
        sleep_run_id=None,
        next_state_key=next_state_key,
    )
    if rule_violation:
        # Re-load and patch the JSONB meta_data so it carries the flag.
        from aether_api.models.episodic_memory import EpisodicMemory
        from sqlalchemy import update as _sql_update

        await session.execute(
            _sql_update(EpisodicMemory)
            .where(EpisodicMemory.id == ep.id)
            .values(meta_data={**dict(ep.meta_data), "rule_violation": True})
        )
        await session.flush()
    if created_at is not None:
        from aether_api.models.episodic_memory import EpisodicMemory
        from sqlalchemy import update as _sql_update

        await session.execute(
            _sql_update(EpisodicMemory)
            .where(EpisodicMemory.id == ep.id)
            .values(created_at=created_at)
        )
        await session.flush()
    return ep


async def _seed_sleep_run(session, *, user_id, project_id):
    from aether_api.sleep.repositories import SleepRunRepository

    repo = SleepRunRepository(session)
    return await repo.create(
        project_id=project_id,
        user_id=user_id,
        phase_type="profundo",
        status="running",
    )


# ---------------------------------------------------------------------------
# 1. Step 5a — pure pass: hand-computed Bellman expectation
# ---------------------------------------------------------------------------


async def test_apply_q_update_pass_computes_bellman_update(app_client) -> None:
    """Two episodes → exactly one Q-cell update per (state, action) chain.

    Uses explicit ``created_at`` stamps to pin the chronological order
    so the test asserts the deterministic Bellman expectation regardless
    of microsecond-level insertion races.
    """
    from datetime import UTC

    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.sleep.learning_step import (
        ALPHA_NORMAL,
        GAMMA,
        apply_q_update_pass,
    )

    user_id, project_id = await _seed_user_project()
    maker = get_session_maker()
    # Pin the two episodes 1 second apart so chronological order is
    # deterministic across runs (otherwise the two NOW() inserts share
    # a timestamp and the tie-break is by id, which is random).
    t1 = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
    t2 = datetime(2026, 5, 28, 12, 0, 1, tzinfo=UTC).replace(tzinfo=None)
    async with maker() as session:
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:A",
            action="buy",
            reward=Decimal("0.5"),
            next_state_key="sk:B",
            created_at=t1,
        )
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:A",
            action="buy",
            reward=Decimal("0.3"),
            next_state_key=None,
            created_at=t2,
        )
        await session.commit()

    async with maker() as session:
        project = await ProjectRepository(session).get_for_user(user_id, project_id)
        run = await _seed_sleep_run(
            session, user_id=user_id, project_id=project_id
        )
        await session.commit()
        result = await apply_q_update_pass(
            session,
            user_id=user_id,
            project=project,
            sleep_run=run,
            since=None,
        )

    # Cold start → old_table empty.
    assert result.old_table == {}
    # Two trades both touch (sk:A, buy); the second observes the first's
    # nudge but max_a' Q(s', a') is 0 (sk:B is unseen, sk:None is terminal).
    # Order pinned via ``created_at``: (0.5, 0.3).
    # Q1 = 0 + 0.15 * (0.5 + 0.92*0 - 0)   = 0.075
    # Q2 = 0.075 + 0.15 * (0.3 + 0.92*0 - 0.075) = 0.075 + 0.03375 = 0.10875
    expected = 0.0 + ALPHA_NORMAL * (0.5 + GAMMA * 0.0 - 0.0)
    expected = expected + ALPHA_NORMAL * (0.3 + GAMMA * 0.0 - expected)
    assert result.trades_processed == 2
    assert result.new_table["sk:A"]["buy"] == pytest.approx(expected, rel=1e-9)
    assert result.special_count == 0
    assert result.alpha_avg == pytest.approx(ALPHA_NORMAL, rel=1e-9)


async def test_apply_q_update_pass_zero_trades_returns_empty(app_client) -> None:
    """No episodes → trades_processed=0, new_table mirrors old_table."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.sleep.learning_step import apply_q_update_pass

    user_id, project_id = await _seed_user_project()
    maker = get_session_maker()
    async with maker() as session:
        project = await ProjectRepository(session).get_for_user(user_id, project_id)
        run = await _seed_sleep_run(
            session, user_id=user_id, project_id=project_id
        )
        await session.commit()
        result = await apply_q_update_pass(
            session,
            user_id=user_id,
            project=project,
            sleep_run=run,
            since=None,
        )
    assert result.trades_processed == 0
    assert result.new_table == {}
    assert result.old_table == {}
    assert result.special_count == 0


# ---------------------------------------------------------------------------
# 2. Step 5b — special tagging (magnitude OR rule_violation flag)
# ---------------------------------------------------------------------------


async def test_apply_q_update_pass_tags_special_by_magnitude(app_client) -> None:
    """``|reward| >= threshold`` ⇒ is_special=True ⇒ α=ALPHA_SPECIAL."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.sleep.learning_step import (
        ALPHA_NORMAL,
        ALPHA_SPECIAL,
        apply_q_update_pass,
    )

    # threshold = 2.0 (orchestrator_params['special_trade_threshold'])
    user_id, project_id = await _seed_user_project()

    maker = get_session_maker()
    async with maker() as session:
        # Below threshold — normal.
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:normal",
            action="buy",
            reward=Decimal("0.5"),
        )
        # Above threshold (positive) — special.
        big_pos = await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:special",
            action="sell",
            reward=Decimal("3.0"),
        )
        # Above threshold (negative magnitude) — special.
        big_neg = await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:specialneg",
            action="sell",
            reward=Decimal("-2.5"),
        )
        await session.commit()

    async with maker() as session:
        project = await ProjectRepository(session).get_for_user(user_id, project_id)
        run = await _seed_sleep_run(
            session, user_id=user_id, project_id=project_id
        )
        await session.commit()
        result = await apply_q_update_pass(
            session,
            user_id=user_id,
            project=project,
            sleep_run=run,
            since=None,
        )

    assert result.trades_processed == 3
    assert result.special_count == 2
    assert set(result.special_episode_ids) == {big_pos.id, big_neg.id}
    # Average α: one normal + two specials.
    expected_avg = (ALPHA_NORMAL + 2 * ALPHA_SPECIAL) / 3
    assert result.alpha_avg == pytest.approx(expected_avg, rel=1e-9)


async def test_apply_q_update_pass_tags_special_by_rule_violation(
    app_client,
) -> None:
    """``meta_data.rule_violation = True`` ⇒ special regardless of magnitude."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.sleep.learning_step import apply_q_update_pass

    user_id, project_id = await _seed_user_project()

    maker = get_session_maker()
    async with maker() as session:
        # Tiny reward but rule_violation flag set — must still be special.
        special_ep = await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:rv",
            action="buy",
            reward=Decimal("0.01"),
            rule_violation=True,
        )
        await session.commit()

    async with maker() as session:
        project = await ProjectRepository(session).get_for_user(user_id, project_id)
        run = await _seed_sleep_run(
            session, user_id=user_id, project_id=project_id
        )
        await session.commit()
        result = await apply_q_update_pass(
            session,
            user_id=user_id,
            project=project,
            sleep_run=run,
            since=None,
        )

    assert result.trades_processed == 1
    assert result.special_count == 1
    assert result.special_episode_ids == [special_ep.id]


# ---------------------------------------------------------------------------
# 3. R1 — atomic 3-write finalize: forced rollback on each path
# ---------------------------------------------------------------------------


async def _count_rows() -> dict[str, int]:
    """Return ``{table: count}`` for the three Phase 7 target tables."""
    from aether_api.db.session import get_engine
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        q = (await conn.execute(text("SELECT count(*) FROM q_tables"))).scalar_one()
        s = (
            await conn.execute(text("SELECT count(*) FROM sleep_reports"))
        ).scalar_one()
        c = (
            await conn.execute(text("SELECT count(*) FROM config_versions"))
        ).scalar_one()
    return {"q_tables": int(q), "sleep_reports": int(s), "config_versions": int(c)}


async def _run_finalize(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    raise_on: str | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
):
    """Drive :func:`finalize_learning_step` against a freshly-seeded run.

    ``raise_on`` ∈ ``{"qtable", "sleep_report", "config_version"}`` — when
    set, the matching repo method is monkey-patched to raise ``RuntimeError``
    so we can assert the rollback semantics. ``None`` runs the happy path.

    ``monkeypatch`` is the pytest fixture — required when ``raise_on``
    is set so the patches are undone at end-of-test (preventing leakage
    into other tests).
    """
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.repositories.q_table_repository import QTableRepository
    from aether_api.repositories.sleep_report_repository import (
        SleepReportRepository,
    )
    from aether_api.sleep.learning_step import (
        apply_q_update_pass,
        finalize_learning_step,
    )
    from aether_api.sleep.repositories import ConfigVersionRepository

    maker = get_session_maker()
    async with maker() as session:
        # Seed a single trade so the pass produces a non-empty Q-Table.
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:fin",
            action="buy",
            reward=Decimal("0.5"),
        )
        await session.commit()

    async with maker() as session:
        project = await ProjectRepository(session).get_for_user(
            user_id, project_id
        )
        run = await _seed_sleep_run(
            session, user_id=user_id, project_id=project_id
        )
        await session.commit()

        pass_result = await apply_q_update_pass(
            session,
            user_id=user_id,
            project=project,
            sleep_run=run,
            since=None,
        )

        if raise_on is not None:
            assert monkeypatch is not None, (
                "raise_on requires a monkeypatch fixture so the patch is undone"
            )
            if raise_on == "qtable":
                async def _boom_q(self, **_kw):  # noqa: ANN001
                    raise RuntimeError("forced: q_tables insert")

                monkeypatch.setattr(QTableRepository, "insert_version", _boom_q)
            elif raise_on == "sleep_report":
                async def _boom_r(self, **_kw):  # noqa: ANN001
                    raise RuntimeError("forced: sleep_reports insert")

                monkeypatch.setattr(SleepReportRepository, "insert", _boom_r)
            elif raise_on == "config_version":
                async def _boom_c(self, **_kw):  # noqa: ANN001
                    raise RuntimeError("forced: config_versions insert")

                monkeypatch.setattr(ConfigVersionRepository, "create", _boom_c)

        try:
            lsr = await finalize_learning_step(
                session,
                user_id=user_id,
                project=project,
                sleep_run=run,
                pass_result=pass_result,
                risk_class="bajo",
                proposed_snapshot={"worker_params": {"sma_window": 30}},
            )
        except RuntimeError as exc:
            # Roll the outer transaction back so the savepoint
            # rollback above is observable to a fresh connection.
            await session.rollback()
            return None, str(exc)
        else:
            await session.commit()
            return lsr, None


async def test_finalize_happy_path_writes_three_rows(app_client) -> None:
    user_id, project_id = await _seed_user_project()
    before = await _count_rows()
    lsr, err = await _run_finalize(user_id=user_id, project_id=project_id)
    after = await _count_rows()
    assert err is None
    assert lsr is not None
    assert after["q_tables"] - before["q_tables"] == 1
    assert after["sleep_reports"] - before["sleep_reports"] == 1
    assert after["config_versions"] - before["config_versions"] == 1
    assert lsr.q_table_version == 1
    assert lsr.risk_class == "bajo"


async def test_finalize_rolls_back_on_qtable_failure(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """R1 — forced fault on q_tables INSERT rolls EVERYTHING back."""
    user_id, project_id = await _seed_user_project()
    before = await _count_rows()
    lsr, err = await _run_finalize(
        user_id=user_id,
        project_id=project_id,
        raise_on="qtable",
        monkeypatch=monkeypatch,
    )
    after = await _count_rows()
    assert lsr is None
    assert err is not None and "q_tables" in err
    assert after["q_tables"] == before["q_tables"]
    assert after["sleep_reports"] == before["sleep_reports"]
    assert after["config_versions"] == before["config_versions"]


async def test_finalize_rolls_back_on_sleep_report_failure(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """R1 — forced fault on sleep_reports INSERT rolls EVERYTHING back."""
    user_id, project_id = await _seed_user_project()
    before = await _count_rows()
    lsr, err = await _run_finalize(
        user_id=user_id,
        project_id=project_id,
        raise_on="sleep_report",
        monkeypatch=monkeypatch,
    )
    after = await _count_rows()
    assert lsr is None
    assert err is not None and "sleep_reports" in err
    assert after["q_tables"] == before["q_tables"]
    assert after["sleep_reports"] == before["sleep_reports"]
    assert after["config_versions"] == before["config_versions"]


async def test_finalize_rolls_back_on_config_version_failure(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """R1 — forced fault on config_versions INSERT rolls EVERYTHING back."""
    user_id, project_id = await _seed_user_project()
    before = await _count_rows()
    lsr, err = await _run_finalize(
        user_id=user_id,
        project_id=project_id,
        raise_on="config_version",
        monkeypatch=monkeypatch,
    )
    after = await _count_rows()
    assert lsr is None
    assert err is not None and "config_versions" in err
    assert after["q_tables"] == before["q_tables"]
    assert after["sleep_reports"] == before["sleep_reports"]
    assert after["config_versions"] == before["config_versions"]


# ---------------------------------------------------------------------------
# 4. Cache invalidation — post-commit only.
# ---------------------------------------------------------------------------


@dataclass
class _SpyCache:
    """Records every ``invalidate`` call so tests can assert on / off."""

    calls: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    def invalidate(self, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        self.calls.append((user_id, project_id))


@dataclass
class _OrchResult:
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


class _OrchFakeEngine:
    """Mock sandbox Engine — minimal shape for ``run_sleep_phase``."""

    def __init__(self, per_agent_returns: dict[str, _OrchResult]) -> None:
        self.per_agent_returns = per_agent_returns
        self.calls: list[tuple[str, str]] = []

    def run_agent(self, *, agent_row, project_row, inputs, dry_run, mode):
        self.calls.append((agent_row.type, mode))
        return self.per_agent_returns[agent_row.type]


async def _seed_full_project_with_agents():
    """Like :func:`_seed_user_project` but with 3 agents wired in."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_agent, seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session,
            email=f"phase7-cache-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        project = await seed_project(session, owner=user)
        project.status = "active"
        project.worker_params = {"sma_window": 30}
        project.investigator_params = {}
        project.auditor_params = {}
        project.orchestrator_params = {"special_trade_threshold": 2.0}
        w = await seed_agent(session, owner=user, name="w", type="worker")
        i = await seed_agent(session, owner=user, name="i", type="investigator")
        a = await seed_agent(session, owner=user, name="a", type="auditor")
        project.worker_agent_id = w.id
        project.investigator_agent_id = i.id
        project.auditor_agent_id = a.id
        await session.commit()
        return user.id, project.id


def _agent_returns() -> dict[str, _OrchResult]:
    return {
        "worker": _OrchResult(
            status="success",
            result={
                "reflection_md": "worker reflection",
                "suggested_changes": {"worker_params": {"sma_window": 32}},
            },
        ),
        "investigator": _OrchResult(
            status="success",
            result={
                "reflection_md": "investigator reflection",
                "suggested_changes": {},
            },
        ),
        "auditor": _OrchResult(
            status="success",
            result={
                "reflection_md": "auditor reflection",
                "suggested_changes": {},
            },
        ),
    }


async def test_cache_invalidated_after_successful_deep_sleep(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """Profundo + learning ON + happy commit → cache.invalidate(user, project) fires."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    # Sandbox flag is already ON via test_orchestrator import side-effects;
    # be explicit here so this test is self-sufficient.
    os.environ["AGENT_SANDBOX_ENABLED"] = "true"
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "true")
    get_settings.cache_clear()

    user_id, project_id = await _seed_full_project_with_agents()

    # Seed one trade so the Q-update pass produces a real row.
    maker = get_session_maker()
    async with maker() as session:
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:cache",
            action="buy",
            reward=Decimal("0.5"),
        )
        await session.commit()

    spy = _SpyCache()
    engine = _OrchFakeEngine(per_agent_returns=_agent_returns())

    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project_id,
            user_id=user_id,
            phase_type="profundo",
            engine=engine,
            learning_cache=spy,
        )

    assert result.status in {"succeeded", "partial"}
    # ONE invalidation, scoped to the right tenant.
    assert spy.calls == [(user_id, project_id)]


async def test_cache_not_invalidated_when_finalize_rolls_back(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """Forced finalize failure ⇒ cache.invalidate MUST NOT be called.

    This guards against the worst failure mode the spec calls out: a
    rolled-back transaction that nonetheless drops the cache slot, which
    would cause every subsequent reader to fetch a stale empty cache
    until the next warm.
    """
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    os.environ["AGENT_SANDBOX_ENABLED"] = "true"
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "true")
    get_settings.cache_clear()

    user_id, project_id = await _seed_full_project_with_agents()

    maker = get_session_maker()
    async with maker() as session:
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:cache-fail",
            action="buy",
            reward=Decimal("0.5"),
        )
        await session.commit()

    spy = _SpyCache()
    engine = _OrchFakeEngine(per_agent_returns=_agent_returns())

    # Force the finalize to raise by patching the q-table insert.
    from aether_api.repositories.q_table_repository import QTableRepository

    async def _boom(self, **_kw):  # noqa: ANN001
        raise RuntimeError("forced: tx rollback path")

    monkeypatch.setattr(QTableRepository, "insert_version", _boom)

    before = await _count_rows()

    async with maker() as session:
        with pytest.raises(RuntimeError):
            await run_sleep_phase(
                session,
                project_id=project_id,
                user_id=user_id,
                phase_type="profundo",
                engine=engine,
                learning_cache=spy,
            )

    after = await _count_rows()
    # No Q-Table / report / config_version rows landed.
    assert after["q_tables"] == before["q_tables"]
    assert after["sleep_reports"] == before["sleep_reports"]
    assert after["config_versions"] == before["config_versions"]
    # AND the cache was never invalidated.
    assert spy.calls == []


# ---------------------------------------------------------------------------
# 5. Gating — AETHER_LEARNING_ENABLED=false skips the entire path.
# ---------------------------------------------------------------------------


async def test_learning_disabled_falls_back_to_legacy_orchestrator(
    monkeypatch: pytest.MonkeyPatch, app_client
) -> None:
    """When the flag is OFF the orchestrator MUST NOT touch q_tables /
    sleep_reports / cache. The legacy ``cv_repo.create`` path runs."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.orchestrator import run_sleep_phase

    os.environ["AGENT_SANDBOX_ENABLED"] = "true"
    monkeypatch.delenv("AETHER_LEARNING_ENABLED", raising=False)
    get_settings.cache_clear()

    user_id, project_id = await _seed_full_project_with_agents()
    maker = get_session_maker()
    async with maker() as session:
        await _seed_episode(
            session,
            user_id=user_id,
            project_id=project_id,
            state_key="sk:off",
            action="buy",
            reward=Decimal("0.5"),
        )
        await session.commit()

    spy = _SpyCache()
    engine = _OrchFakeEngine(per_agent_returns=_agent_returns())

    before = await _count_rows()
    async with maker() as session:
        result = await run_sleep_phase(
            session,
            project_id=project_id,
            user_id=user_id,
            phase_type="profundo",
            engine=engine,
            learning_cache=spy,
        )

    after = await _count_rows()
    assert result.status in {"succeeded", "partial"}
    # No Q-Table / sleep_report rows when learning is OFF.
    assert after["q_tables"] == before["q_tables"]
    assert after["sleep_reports"] == before["sleep_reports"]
    # A standard config_versions row MAY still land via the legacy path
    # since the snapshot diff is non-empty — that's fine, the assertion
    # is just that the *learning* writes did not.
    # AND cache was never invalidated.
    assert spy.calls == []
