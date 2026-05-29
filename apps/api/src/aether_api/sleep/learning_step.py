"""Sleep-learning step (Phase 7 of ``sleep-learning-loop``).

This module owns the three sub-steps the deep-sleep synthesis adds on
top of the v1 sleep workflow:

* **5a Q-update pass** (:func:`apply_q_update_pass`) — pulls every
  unconsumed episode since the previous sleep run, loads the latest
  ``q_tables`` row, applies the canonical Bellman update in memory and
  returns a :class:`QUpdatePass` envelope. No DB writes happen here —
  the pass is idempotent within a single ``sleep_run``.

* **5b special-trade tagging** — folded into the same pass for locality.
  An episode is "special" when ``|reward| >= threshold`` OR its meta
  payload carries ``rule_violation = True``. ``threshold`` is read from
  the project's ``orchestrator_params['special_trade_threshold']`` JSONB
  key, falling back to ``1.5 × project.risk_per_trade``.
  Special trades use ``α = 0.35``; everyone else uses ``α = 0.15``.

* **5c atomic 3-write transaction** (:func:`finalize_learning_step`) —
  wraps ``q_tables.INSERT`` + ``sleep_reports.INSERT`` +
  ``config_versions.INSERT`` (the promotion row) plus the
  ``mark_special`` UPDATE inside a SINGLE transaction. Failure on ANY
  of the four paths rolls everything back — no partial Q-Table version
  ever survives a crashed finalize.

Gating
------

The whole module is opt-in via the ``AETHER_LEARNING_ENABLED`` env
flag. When the flag is OFF the orchestrator skips this code path
entirely; the existing sleep workflow is unchanged.

The flag is read at call sites (orchestrator), not here, so the unit
tests for this module don't have to twiddle env state.

Cache invalidation
------------------

The post-commit cache invalidation lives in :mod:`aether_api.sleep.orchestrator`
because the cache reference is owned by ``app.state`` (lifespan). This
module is pure: it produces the new Q-Table dict + writes; the caller
runs the side-effect that drops the stale cache slot.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.learning.q_learning import q_update
from aether_api.learning.qtable_versioning import classify_qtable_delta
from aether_api.models.project import Project
from aether_api.models.sleep_run import SleepRun
from aether_api.repositories.episodic_memory_repository import (
    EpisodicMemoryRepository,
)
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.sleep_report_repository import SleepReportRepository
from aether_api.sleep.repositories import ConfigVersionRepository

logger = logging.getLogger(__name__)

__all__ = [
    "ALPHA_NORMAL",
    "ALPHA_SPECIAL",
    "GAMMA",
    "LearningStepResult",
    "QUpdatePass",
    "apply_q_update_pass",
    "finalize_learning_step",
    "is_learning_enabled",
]


# ---------------------------------------------------------------------------
# Constants — pinned by the canonical sleep-learning spec (engram #2069).
# ---------------------------------------------------------------------------

#: Discount factor used for every TD update in the sleep loop.
GAMMA: float = 0.92

#: Learning rate for ordinary (non-special) episodes.
ALPHA_NORMAL: float = 0.15

#: Learning rate for special (rule-violating or large-magnitude) episodes.
ALPHA_SPECIAL: float = 0.35


# ---------------------------------------------------------------------------
# Env helpers.
# ---------------------------------------------------------------------------


def is_learning_enabled() -> bool:
    """Return True iff ``AETHER_LEARNING_ENABLED`` is set to a truthy value.

    Matches :func:`aether_api.sandbox.engine._learning_enabled_from_env`
    semantics so the flag has a single, consistent shape across the
    codebase.
    """
    raw = os.environ.get("AETHER_LEARNING_ENABLED")
    if raw is None:
        return False
    return raw.strip().lower() in {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# Dataclasses.
# ---------------------------------------------------------------------------


@dataclass
class QUpdatePass:
    """Outcome of one in-memory Q-update pass over the episodic window.

    ``new_table`` is the candidate Q-Table to persist on the new version
    row. ``old_table`` is the prior (cached) Q-Table or an empty dict on
    cold-start — kept on the envelope so the classifier and the report
    have a single source of truth.
    """

    new_table: dict[str, dict[str, float]]
    old_table: dict[str, dict[str, float]]
    trades_processed: int
    total_reward: float
    special_count: int
    special_episode_ids: list[uuid.UUID] = field(default_factory=list)
    alpha_sum: float = 0.0

    @property
    def alpha_avg(self) -> float:
        """Mean α used across the pass (defaults to 0 when no trades)."""
        if self.trades_processed == 0:
            return 0.0
        return self.alpha_sum / self.trades_processed


@dataclass
class LearningStepResult:
    """What :func:`finalize_learning_step` returns to the caller.

    Carries the newly-persisted row identifiers + the risk class so the
    orchestrator can stamp its summary string and decide whether to
    invalidate the cache.
    """

    q_table_id: uuid.UUID
    q_table_version: int
    sleep_report_id: uuid.UUID
    config_version_id: uuid.UUID
    risk_class: str
    special_marked: int


# ---------------------------------------------------------------------------
# Step 5a + 5b — in-memory Q-update pass.
# ---------------------------------------------------------------------------


def _special_threshold(project: Project) -> float:
    """Return the |reward| threshold above which a trade is "special".

    Resolution order:

    1. ``project.orchestrator_params['special_trade_threshold']`` (JSONB
       key — the operator can override per project without a migration).
    2. ``1.5 × project.risk_per_trade`` (the default — a reward
       50 % above the project's per-trade risk budget is large enough to
       deserve the higher learning rate).
    3. ``1.5 × 1.0`` (1.5) when both are missing (defensive — keeps the
       caller from crashing on a freshly-created project).
    """
    params = getattr(project, "orchestrator_params", None) or {}
    raw = params.get("special_trade_threshold") if isinstance(params, dict) else None
    if raw is not None:
        try:
            override = float(raw)
        except (TypeError, ValueError):
            override = None
        if override is not None and override > 0:
            return override

    rpt = getattr(project, "risk_per_trade", None)
    if isinstance(rpt, Decimal):
        try:
            rpt_f = float(rpt)
        except (ValueError, ArithmeticError):
            rpt_f = 1.0
    elif isinstance(rpt, (int, float)) and not isinstance(rpt, bool):
        rpt_f = float(rpt)
    else:
        rpt_f = 1.0
    return 1.5 * rpt_f


def _max_q_for_state(table: dict[str, dict[str, float]], state_key: str | None) -> float:
    """Return ``max_a' Q(s', a')`` or 0.0 when ``s'`` is terminal/unseen.

    Per spec ``max_next_q`` is ``0.0`` for terminal transitions; the
    same value is used when the next state has not been visited yet
    (i.e. the cell is missing from the table).
    """
    if state_key is None:
        return 0.0
    cell = table.get(state_key)
    if not isinstance(cell, dict) or not cell:
        return 0.0
    numeric: list[float] = []
    for value in cell.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            numeric.append(float(value))
        elif isinstance(value, Decimal):
            try:
                numeric.append(float(value))
            except (ValueError, ArithmeticError):
                continue
    return max(numeric) if numeric else 0.0


def _existing_q(table: dict[str, dict[str, float]], state_key: str, action: str) -> float:
    """Return ``Q(s, a)`` from ``table`` or ``0.0`` when the cell is empty."""
    cell = table.get(state_key)
    if not isinstance(cell, dict):
        return 0.0
    value = cell.get(action)
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (ValueError, ArithmeticError):
            return 0.0
    return 0.0


def _strip_meta(table_data: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """Return a copy of ``table_data`` without the ``__meta__`` stash key.

    The repository stores caller-supplied ``metadata`` under
    ``table_data["__meta__"]`` for free; the Q-update math does not
    want that key polluting the state-space iteration.
    """
    if not isinstance(table_data, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for state_key, cell in table_data.items():
        if state_key == "__meta__":
            continue
        if isinstance(cell, dict):
            out[state_key] = dict(cell)
    return out


async def apply_q_update_pass(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    project: Project,
    sleep_run: SleepRun,
    since: datetime | None,
) -> QUpdatePass:
    """Run Step 5a + 5b end-to-end and return the candidate Q-Table.

    Idempotent within a single ``sleep_run`` — this function performs
    NO database writes. The orchestrator threads the returned
    :class:`QUpdatePass` into :func:`finalize_learning_step` which is
    the only path that persists state.

    Parameters
    ----------
    session
        Async session used for the read-only repo calls (latest
        Q-Table + episodic window). The function never mutates this
        session.
    user_id
        Tenant scope for every repo call. Cross-tenant ``project`` is
        treated as "no data" — repos return ``[]`` / ``None``.
    project
        The project being deep-slept. Needed for the special-trade
        threshold (``orchestrator_params['special_trade_threshold']``
        falling back to ``1.5 × risk_per_trade``).
    sleep_run
        The current sleep run. Used only to bound the chronological
        window with ``since=last_run`` if the caller didn't supply
        one explicitly.
    since
        Lower-bound timestamp for the episodic pull. Inclusive on
        ``created_at``. ``None`` → load every unconsumed episode
        (first sleep run of a fresh project).
    """
    episodic_repo = EpisodicMemoryRepository(session)
    qtable_repo = QTableRepository(session)

    # 1. Load the latest Q-Table (cold start → empty dict).
    old_row = await qtable_repo.get_latest(user_id=user_id, project_id=project.id)
    old_table = _strip_meta(old_row.table_data if old_row is not None else None)

    # We work on a deep-ish copy so the caller's reference to the old
    # table (e.g. for the sleep_report payload) doesn't drift.
    new_table: dict[str, dict[str, float]] = {
        sk: dict(cell) for sk, cell in old_table.items()
    }

    # 2. Pull every episode in the window. The repo paginates by
    # ``limit``; the deep-sleep pass wants ALL of them, so we use a
    # generous cap (matches the design — the spec calls this the
    # "every closed trade since the previous sleep_run" path).
    until = datetime.now(tz=UTC).replace(tzinfo=None)
    episodes = await episodic_repo.list_by_project(
        user_id=user_id,
        project_id=project.id,
        since=since,
        until=until,
        limit=10_000,
        offset=0,
    )

    # The repo orders DESC for the read-path UI; the Q-update needs
    # chronological order so a later trade observes the policy nudge
    # from earlier ones in the same pass.
    episodes_chrono = sorted(
        episodes,
        key=lambda e: (e.created_at, e.id),
    )

    # 3. Resolve the special-trade threshold once — every per-trade
    # decision then re-uses it.
    threshold = _special_threshold(project)

    total_reward = 0.0
    special_count = 0
    special_ids: list[uuid.UUID] = []
    alpha_sum = 0.0
    trades_processed = 0

    for episode in episodes_chrono:
        # Episode shape:
        # ``state_key`` / ``action`` / ``reward`` / ``next_state_key``
        # — plus ``meta_data`` JSONB. The JSONB may carry an explicit
        # ``rule_violation`` flag that pins is_special=True regardless
        # of magnitude.
        reward = float(episode.reward) if episode.reward is not None else 0.0
        rule_violation = bool((episode.meta_data or {}).get("rule_violation", False))

        is_special = (abs(reward) >= threshold) or rule_violation
        alpha = ALPHA_SPECIAL if is_special else ALPHA_NORMAL

        old_q = _existing_q(new_table, episode.state_key, episode.action)
        max_next_q = _max_q_for_state(new_table, episode.next_state_key)
        new_q = q_update(
            q_value=old_q,
            reward=reward,
            max_next_q=max_next_q,
            alpha=alpha,
            gamma=GAMMA,
        )

        cell = new_table.setdefault(episode.state_key, {})
        cell[episode.action] = new_q

        total_reward += reward
        alpha_sum += alpha
        trades_processed += 1
        if is_special:
            special_count += 1
            special_ids.append(episode.id)

    return QUpdatePass(
        new_table=new_table,
        old_table=old_table,
        trades_processed=trades_processed,
        total_reward=total_reward,
        special_count=special_count,
        special_episode_ids=special_ids,
        alpha_sum=alpha_sum,
    )


# ---------------------------------------------------------------------------
# Step 5c — atomic 3-write finalize.
# ---------------------------------------------------------------------------


async def _next_version(
    qtable_repo: QTableRepository, *, user_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    """Return ``max(version) + 1`` for the project, or 1 on first version.

    Tenant-scoped via the repo's JOIN; a cross-tenant probe sees no
    rows and would (incorrectly) collide on version=1. We refuse to
    write in that path elsewhere (``insert_version`` raises
    ``PermissionError`` on cross-tenant), so the version here is safe
    even on the cold-start branch.
    """
    latest = await qtable_repo.get_latest(user_id=user_id, project_id=project_id)
    return 1 if latest is None else latest.version + 1


def _classify_pass(
    *,
    pass_result: QUpdatePass,
    project: Project,
    top_k_states: list[tuple[str, int]],
) -> str:
    """Run the Q-Table classifier over the pass result.

    Returns the canonical risk class string (``"bajo" / "medio" /
    "alto"``). Kept as a single helper so the orchestrator can call it
    BEFORE opening the transaction (the spec mandates classifier runs
    BEFORE the tx so the risk_class is known when we write the
    config_versions row).
    """
    return classify_qtable_delta(
        old_table=pass_result.old_table,
        new_table=pass_result.new_table,
        project=project,
        top_k_states=top_k_states,
    )


async def finalize_learning_step(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    project: Project,
    sleep_run: SleepRun,
    pass_result: QUpdatePass,
    risk_class: str,
    prompt_snapshot: str | None = None,
    version_name: str | None = None,
    parent_config_version_id: uuid.UUID | None = None,
    auditor_metrics: dict[str, Any] | None = None,
    worker_insights: dict[str, Any] | None = None,
    improvements_applied: list[Any] | None = None,
    summary_md: str | None = None,
    overall_score: float = 0.0,
    proposed_snapshot: dict[str, Any] | None = None,
) -> LearningStepResult:
    """Persist the Q-Table + sleep report + config version in ONE transaction.

    Atomicity guarantee
    -------------------

    All four writes (Q-Table INSERT, sleep_report INSERT,
    config_versions INSERT, episodic_memory mark-special UPDATE) share
    a single ``session.begin_nested()`` savepoint. Any exception inside
    the block rolls EVERYTHING back — no partial Q-Table version
    survives a crashed finalize. The caller is expected to ``commit``
    the outer transaction; on success the savepoint is released and
    the writes become visible.

    We use ``begin_nested()`` rather than ``begin()`` because the
    orchestrator may already have an open transaction (the unit of
    work pattern SQLAlchemy uses for AsyncSession means the session
    has an implicit transaction the moment you flush). A nested
    savepoint composes safely either way.

    Risk class
    ----------

    ``risk_class`` is computed by the caller BEFORE the transaction
    via :func:`_classify_pass` (or equivalently
    :func:`aether_api.learning.classify_qtable_delta`). When the class
    is ``"alto"`` the config_version lands as ``status='pending'``
    (the existing approval-gate convention from ``sleep/applier.py``);
    otherwise it lands as ``status='applied'`` so low-risk passes
    apply automatically without an operator click.

    Returns
    -------
    LearningStepResult
        The row identifiers + risk_class + number of special episodes
        actually marked. The orchestrator uses this to build the
        ``OrchestratorResult.summary`` and to decide whether to
        invalidate the cache (cache invalidation is post-commit, see
        the caller).
    """
    # Default mutable containers to avoid surprising shared state.
    auditor_metrics = dict(auditor_metrics or {})
    worker_insights = dict(worker_insights or {})
    improvements_applied = list(improvements_applied or [])
    proposed_snapshot = dict(proposed_snapshot or {})

    qtable_repo = QTableRepository(session)
    report_repo = SleepReportRepository(session)
    cv_repo = ConfigVersionRepository(session)
    episodic_repo = EpisodicMemoryRepository(session)

    # Resolve the next version OUTSIDE the savepoint so any read
    # failure does not enter the rollback path with mutations queued.
    next_version = await _next_version(
        qtable_repo, user_id=user_id, project_id=project.id
    )

    # ``alto`` → operator must approve. Anything else → auto-applied
    # so the project's mutable surface reflects the new Q-Table
    # version immediately.
    status = "pending" if risk_class == "alto" else "applied"
    now = datetime.now(tz=UTC).replace(tzinfo=None)

    # The metadata stash on the Q-Table row carries the trade counts
    # and the average α so the report can read them back without
    # rejoining episodic_memory.
    qtable_metadata: dict[str, Any] = {
        "trades_processed": pass_result.trades_processed,
        "total_reward": pass_result.total_reward,
        "special_count": pass_result.special_count,
        "alpha_avg": pass_result.alpha_avg,
    }

    # Snapshot for the config_versions row — start from the caller's
    # proposed snapshot and inject the q_table version pointer so the
    # Sleep Phase revert path can roll Q-Tables back too.
    cv_snapshot: dict[str, Any] = dict(proposed_snapshot)
    cv_snapshot["q_table_version"] = next_version

    async with session.begin_nested() as savepoint:
        try:
            q_row = await qtable_repo.insert_version(
                user_id=user_id,
                project_id=project.id,
                version=next_version,
                table_data=pass_result.new_table,
                learning_rate=pass_result.alpha_avg if pass_result.alpha_avg > 0 else ALPHA_NORMAL,
                discount_factor=GAMMA,
                metadata=qtable_metadata,
                sleep_run_id=sleep_run.id,
                episode_count=pass_result.trades_processed,
            )

            report_row = await report_repo.insert(
                user_id=user_id,
                sleep_run_id=sleep_run.id,
                summary=summary_md,
                auditor_metrics=auditor_metrics,
                worker_insights=worker_insights,
                improvements_applied=improvements_applied,
                q_table_before=pass_result.old_table,
                q_table_after=pass_result.new_table,
                overall_score=overall_score,
            )

            cv_row = await cv_repo.create(
                project_id=project.id,
                snapshot=cv_snapshot,
                risk_class=risk_class,
                status=status,
                sleep_run_id=sleep_run.id,
                parent_version_id=parent_config_version_id,
            )

            # Stamp the q_table_version + prompt_snapshot + version_name
            # columns the migration added in 0011. These live on the
            # ConfigVersion model but the repository.create signature
            # predates the columns — set them directly on the ORM row
            # before the savepoint releases.
            cv_row.q_table_version = f"v{next_version}"
            if prompt_snapshot is not None:
                cv_row.prompt_snapshot = prompt_snapshot
            if version_name is not None:
                cv_row.version_name = version_name
            # For auto-applied (non-alto) rows, also stamp applied_at +
            # decided_at = NOW so the operator UI shows the row as a
            # completed promotion, not a pending approval.
            if status == "applied":
                cv_row.applied_at = now
                cv_row.decided_at = now
            await session.flush()

            # 5d — special-trade marker UPDATE. Runs inside the SAME
            # savepoint so a crash here also rolls back the Q-Table +
            # report + config_version writes.
            special_marked = 0
            if pass_result.special_episode_ids:
                special_marked = await episodic_repo.mark_special(
                    user_id=user_id,
                    project_id=project.id,
                    episode_ids=pass_result.special_episode_ids,
                )

        except BaseException:
            # Roll the savepoint back explicitly so the exception
            # propagates with a clean session. ``__aexit__`` would do
            # this too, but being explicit makes the contract obvious
            # to readers.
            await savepoint.rollback()
            raise

    return LearningStepResult(
        q_table_id=q_row.id,
        q_table_version=next_version,
        sleep_report_id=report_row.id,
        config_version_id=cv_row.id,
        risk_class=risk_class,
        special_marked=special_marked,
    )
