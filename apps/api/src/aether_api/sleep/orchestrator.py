"""Sleep Phase workflow orchestrator.

Public entry point: :func:`run_sleep_phase`. Drives one Micro / Profundo
/ Crítico run end-to-end:

1. Verify the project is in a runnable state (``active`` or
   ``maintenance``); if not, write a ``status='skipped'`` row and
   return.
2. Transition the project to ``maintenance`` (preserving the previous
   state for the wake step) and insert a ``status='running'`` row in
   ``sleep_runs``.
3. For each agent assigned to the project (Worker, Investigator,
   Auditor), invoke the sandbox engine with
   ``mode='reflection'``. Capture the reflection markdown +
   suggested_changes JSON.
4. Synthesise the per-agent suggestions into a single proposed
   snapshot, classify the risk, and append a ``status='pending'``
   ``config_versions`` row.
5. Restore the project to its previous status, finalise the
   ``sleep_runs`` row.

Hard invariants:

* The sandbox engine is the ONLY way agent code runs. When
  ``AGENT_SANDBOX_ENABLED`` is False, the orchestrator fast-fails the
  run with ``error='sandbox-disabled'`` and ``status='failed'``.
* Reflections that throw inside the engine are tolerated — the run
  ends ``partial`` if *any* agent reflection fails but *some*
  succeeded; ``failed`` if all fail.
* Auto-apply is OFF in v1. Every proposed snapshot lands as
  ``status='pending'``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.models.agent import Agent
from aether_api.models.project import Project
from aether_api.models.sleep_run import SleepRun
from aether_api.repositories.agent_repository import AgentRepository
from aether_api.repositories.episodic_memory_repository import (
    EpisodicMemoryRepository,
)
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.sleep.classifier import classify_changes
from aether_api.sleep.learning_step import (
    LearningStepResult,
    apply_q_update_pass,
    finalize_learning_step,
    is_learning_enabled,
)
from aether_api.sleep.repositories import (
    ConfigVersionRepository,
    SleepReflectionRepository,
    SleepRunRepository,
)
from aether_api.sleep.snapshot import diff_keys, take_snapshot

logger = logging.getLogger(__name__)

#: Statuses the orchestrator considers runnable. Anything else (e.g.
#: 'stopped', 'error', 'inactive') causes the run to be ``skipped``.
_RUNNABLE_STATUSES: Final[frozenset[str]] = frozenset({"active", "maintenance"})

#: Canonical reflection mode passed through to the sandbox engine.
_REFLECTION_MODE: Final[str] = "reflection"

#: Closed enum of phase types the orchestrator accepts.
PHASE_TYPES: Final[frozenset[str]] = frozenset({"micro", "profundo", "critico"})

#: Mapping of (agent type → column on Project that holds the agent_id).
_AGENT_TYPE_TO_FK: Final[dict[str, str]] = {
    "worker": "worker_agent_id",
    "investigator": "investigator_agent_id",
    "auditor": "auditor_agent_id",
}


@dataclass
class OrchestratorResult:
    """What the orchestrator returns to the caller (manual trigger / scheduler).

    Always carries the SleepRun id so the operator UI can poll the
    detail endpoint for the full reflections + proposed snapshot.
    """

    sleep_run_id: uuid.UUID
    status: str
    summary: str | None = None
    error: str | None = None
    config_version_id: uuid.UUID | None = None


def _build_engine() -> Any:  # noqa: ANN401 — Engine is the late import
    """Construct the sandbox Engine with settings-driven rlimits.

    Late-imported so the sleep package is importable in an environment
    that hasn't yet provisioned multiprocessing's spawn helpers (CI
    containers without /dev/shm).
    """
    from aether_api.sandbox.engine import Engine

    settings = get_settings()
    return Engine(
        wall_clock_seconds=settings.agent_sandbox_wall_clock_seconds,
        rlimit_cpu_seconds=settings.agent_sandbox_rlimit_cpu_seconds,
        rlimit_as_bytes=settings.agent_sandbox_rlimit_as_bytes,
        rlimit_nofile=settings.agent_sandbox_rlimit_nofile,
        rlimit_fsize=settings.agent_sandbox_rlimit_fsize_bytes,
    )


async def _resolve_agents_for_project(
    session: AsyncSession, project: Project
) -> dict[str, Agent]:
    """Return ``{agent_type: Agent}`` for every assigned agent on the project.

    Missing assignments are silently dropped — a project without an
    Investigator agent simply runs the Sleep Phase without one.
    """
    repo = AgentRepository(session)
    out: dict[str, Agent] = {}
    for agent_type, fk_field in _AGENT_TYPE_TO_FK.items():
        agent_id = getattr(project, fk_field, None)
        if agent_id is None:
            continue
        agent = await repo.get_for_user(project.user_id, agent_id)
        if agent is None:
            # Stale FK (agent deleted out-of-band) — log + skip.
            logger.warning(
                "sleep.orchestrator: project %s references missing agent %s (%s)",
                project.id,
                agent_id,
                agent_type,
            )
            continue
        out[agent_type] = agent
    return out


def _parse_suggested_changes(raw: Any) -> dict[str, Any]:
    """Turn whatever the agent returned into a JSON-safe dict.

    Agents are allowed to return either a dict directly OR a JSON string
    embedded in their result (the sandbox passes the return value
    through as-is). We tolerate both shapes and drop everything else.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_reflection_pieces(
    engine_result: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Extract ``(reflection_md, suggested_changes)`` from the engine result.

    The reflection entrypoint contract:

        return {
            "reflection_md": "...",          # optional
            "suggested_changes": { ... },    # required for a useful run
        }

    Missing / malformed fields default to None / {} respectively.
    """
    if engine_result.status != "success":
        return None, {}

    payload = engine_result.result
    if not isinstance(payload, dict):
        return None, {}

    md = payload.get("reflection_md")
    if md is not None and not isinstance(md, str):
        md = None

    suggested = _parse_suggested_changes(payload.get("suggested_changes"))
    return md, suggested


def _merge_proposed_snapshot(
    *,
    current: dict[str, Any],
    suggestions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the proposed snapshot from per-agent suggestion dicts.

    Strategy: start from the current snapshot, overlay each agent's
    suggestions in (worker → investigator → auditor) order. The Auditor
    wins ties — that mirrors the charter's escalation order where the
    Auditor has the last word on risk.

    Per-agent suggestions ARE allowed to nest into the *_params buckets
    (e.g. ``{"worker_params": {"sma_window": 30}}``); we deep-merge dict
    values one level deep to support that shape.
    """
    out: dict[str, Any] = dict(current)
    order = ("worker", "investigator", "auditor")
    for agent_type in order:
        delta = suggestions.get(agent_type) or {}
        for key, value in delta.items():
            if (
                key in {"worker_params", "investigator_params", "auditor_params"}
                and isinstance(value, dict)
                and isinstance(out.get(key), dict)
            ):
                merged = dict(out[key])
                merged.update(value)
                out[key] = merged
            else:
                out[key] = value
    return out


async def _restore_project_status(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    from_status: str,
    to_status: str,
) -> None:
    """Move the project back to ``to_status`` if it's still in ``from_status``.

    Tolerant of concurrent moves: a row that's been moved by another
    request (e.g. the user clicked Stop while sleep was running) keeps
    its newer status — we don't second-guess the operator.
    """
    repo = ProjectRepository(session)
    await repo.update_status_if(
        user_id, project_id, from_status=from_status, to_status=to_status
    )


async def run_sleep_phase(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    phase_type: str,
    engine: Any | None = None,
    learning_cache: Any | None = None,
) -> OrchestratorResult:
    """Execute one Micro / Profundo / Crítico sleep run end-to-end.

    ``engine`` is the sandbox engine instance to use. Tests inject a
    mock here; production callers leave it None and the orchestrator
    builds the default Engine from settings.

    Returns an :class:`OrchestratorResult` regardless of outcome — the
    caller is responsible for HTTP status mapping.
    """
    if phase_type not in PHASE_TYPES:
        raise ValueError(f"unknown phase_type: {phase_type!r}")

    settings = get_settings()
    run_repo = SleepRunRepository(session)
    proj_repo = ProjectRepository(session)

    project = await proj_repo.get_for_user(user_id, project_id)
    if project is None:
        # Caller (router) should have already 404'd, but be defensive.
        raise ValueError("project not found for user")

    # Hard pre-check: sandbox feature flag.
    if not settings.agent_sandbox_enabled:
        run = await run_repo.create(
            project_id=project.id,
            user_id=user_id,
            phase_type=phase_type,
            status="running",
        )
        await run_repo.finalize(
            run_id=run.id,
            status="failed",
            error="sandbox-disabled",
            summary=None,
        )
        await session.commit()
        return OrchestratorResult(
            sleep_run_id=run.id,
            status="failed",
            error="sandbox-disabled",
        )

    # Pre-check: status admits a sleep run.
    previous_status = project.status
    if previous_status not in _RUNNABLE_STATUSES:
        run = await run_repo.create(
            project_id=project.id,
            user_id=user_id,
            phase_type=phase_type,
            status="running",
        )
        await run_repo.finalize(
            run_id=run.id,
            status="skipped",
            summary=f"project status {previous_status!r} not runnable",
        )
        await session.commit()
        return OrchestratorResult(
            sleep_run_id=run.id,
            status="skipped",
            summary=f"project status {previous_status!r} not runnable",
        )

    # Open the run row first so a crash at any point downstream is
    # recoverable by the boot sweep.
    run = await run_repo.create(
        project_id=project.id,
        user_id=user_id,
        phase_type=phase_type,
        status="running",
    )

    # Transition to maintenance for the duration of the run. We commit
    # the status flip so concurrent reads (the dashboard) see the
    # project as paused IMMEDIATELY.
    if previous_status == "active":
        await proj_repo.update_status_if(
            user_id,
            project.id,
            from_status="active",
            to_status="maintenance",
        )
    await session.commit()

    # Re-fetch the project so subsequent in-flight mutations apply to a
    # fresh ORM instance.
    project = await proj_repo.get_for_user(user_id, project_id)
    assert project is not None

    return await _execute_run(
        session,
        engine=engine or _build_engine(),
        project=project,
        run=run,
        previous_status=previous_status,
        phase_type=phase_type,
        learning_cache=learning_cache,
    )


async def _execute_run(
    session: AsyncSession,
    *,
    engine: Any,
    project: Project,
    run: SleepRun,
    previous_status: str,
    phase_type: str,
    learning_cache: Any | None = None,
) -> OrchestratorResult:
    """Inner loop: invoke each agent, synthesise, persist, restore."""
    refl_repo = SleepReflectionRepository(session)
    cv_repo = ConfigVersionRepository(session)

    agents = await _resolve_agents_for_project(session, project)
    if not agents:
        await SleepRunRepository(session).finalize(
            run_id=run.id,
            status="failed",
            error="no agents assigned to project",
        )
        await _restore_project_status(
            session,
            user_id=project.user_id,
            project_id=project.id,
            from_status="maintenance",
            to_status=previous_status,
        )
        await session.commit()
        return OrchestratorResult(
            sleep_run_id=run.id,
            status="failed",
            error="no agents assigned to project",
        )

    # Per-agent reflections — invoked sequentially so a slow agent
    # doesn't fan out to its peers (sandbox subprocesses are heavy).
    per_agent_suggestions: dict[str, dict[str, Any]] = {}
    n_success = 0
    n_failed = 0
    for agent_type, agent in agents.items():
        try:
            engine_result = await asyncio.to_thread(
                engine.run_agent,
                agent_row=agent,
                project_row=project,
                inputs={"phase_type": phase_type},
                dry_run=True,
                mode=_REFLECTION_MODE,
            )
        except Exception as exc:  # noqa: BLE001 — never let one agent kill the run
            logger.exception(
                "sleep.orchestrator: %s reflection raised", agent_type
            )
            await refl_repo.upsert(
                sleep_run_id=run.id,
                agent_type=agent_type,
                reflection_md=f"engine error: {exc!r}",
                suggested_changes={},
            )
            n_failed += 1
            continue

        md, suggested = _extract_reflection_pieces(engine_result)
        await refl_repo.upsert(
            sleep_run_id=run.id,
            agent_type=agent_type,
            reflection_md=md,
            suggested_changes=suggested,
        )
        if engine_result.status == "success":
            n_success += 1
            per_agent_suggestions[agent_type] = suggested
        else:
            n_failed += 1

    if n_success == 0:
        # All reflections failed → nothing to synthesise.
        await SleepRunRepository(session).finalize(
            run_id=run.id,
            status="failed",
            error="all agent reflections failed",
        )
        await _restore_project_status(
            session,
            user_id=project.user_id,
            project_id=project.id,
            from_status="maintenance",
            to_status=previous_status,
        )
        await session.commit()
        return OrchestratorResult(
            sleep_run_id=run.id,
            status="failed",
            error="all agent reflections failed",
        )

    # Synthesise the proposed snapshot. Even when there are zero
    # suggestions (all agents reflected but proposed nothing), we still
    # write a config_versions row so the operator UI shows the run as
    # producing a no-op — easier to reason about than "sometimes there
    # is a row, sometimes there isn't".
    current_snapshot = take_snapshot(project)
    proposed_snapshot = _merge_proposed_snapshot(
        current=current_snapshot,
        suggestions=per_agent_suggestions,
    )

    config_version_id: uuid.UUID | None = None
    learning_result: LearningStepResult | None = None

    # Phase 7 of ``sleep-learning-loop``: when this is a deep-sleep run
    # AND the learning flag is enabled, we replace the standard
    # ``cv_repo.create`` write with the atomic 3-write finalize. The
    # finalize wraps:
    #
    #   q_tables INSERT + sleep_reports INSERT + config_versions INSERT
    #   (+ episodic_memory mark-special UPDATE)
    #
    # in a single savepoint so a crash on ANY path rolls everything
    # back. When the flag is OFF (or this is a Micro/Crítico run) the
    # original behaviour is unchanged.
    if phase_type == "profundo" and is_learning_enabled():
        learning_result = await _run_learning_step(
            session,
            project=project,
            run=run,
            current_snapshot=current_snapshot,
            proposed_snapshot=proposed_snapshot,
            per_agent_suggestions=per_agent_suggestions,
        )
        config_version_id = learning_result.config_version_id
    elif diff_keys(current=current_snapshot, proposed=proposed_snapshot):
        # Find the parent (latest applied) version for lineage.
        parent = await cv_repo.latest_applied_for_project(project.id)
        risk_class = classify_changes(
            current=current_snapshot, proposed=proposed_snapshot
        )
        version_row = await cv_repo.create(
            project_id=project.id,
            snapshot=proposed_snapshot,
            risk_class=risk_class,
            status="pending",
            sleep_run_id=run.id,
            parent_version_id=parent.id if parent is not None else None,
        )
        config_version_id = version_row.id

    final_status = "succeeded" if n_failed == 0 else "partial"
    summary = (
        f"agents: {n_success} succeeded, {n_failed} failed; "
        f"proposed_config_version={'yes' if config_version_id else 'no'}"
    )
    if learning_result is not None:
        summary += (
            f"; q_table_version={learning_result.q_table_version}"
            f"; risk_class={learning_result.risk_class}"
            f"; special_marked={learning_result.special_marked}"
        )

    await SleepRunRepository(session).finalize(
        run_id=run.id, status=final_status, summary=summary
    )

    await _restore_project_status(
        session,
        user_id=project.user_id,
        project_id=project.id,
        from_status="maintenance",
        to_status=previous_status,
    )

    # Update last_sleep_at on the project (best-effort — failure here
    # does not roll back the run row).
    from datetime import datetime

    from sqlalchemy import update as _sql_update

    await session.execute(
        _sql_update(Project)
        .where(Project.id == project.id)
        .where(Project.user_id == project.user_id)
        .values(last_sleep_at=datetime.now(tz=UTC).replace(tzinfo=None))
    )

    await session.commit()

    # Phase 7 of ``sleep-learning-loop``: invalidate the learning cache
    # ONLY after the outer transaction has committed. If anything in
    # the finalize block raised, the transaction would have rolled back
    # above and ``learning_result`` would be None — the cache stays
    # warm with the prior (still-canonical) Q-Table. This is the
    # invariant the spec calls out: a failed transaction MUST NOT
    # invalidate the cache.
    if learning_result is not None and learning_cache is not None:
        try:
            learning_cache.invalidate(project.user_id, project.id)
        except Exception:  # noqa: BLE001 — cache invalidation is best-effort.
            logger.exception(
                "sleep.orchestrator: learning_cache.invalidate raised; "
                "next read will return stale data until TTL expires"
            )

    return OrchestratorResult(
        sleep_run_id=run.id,
        status=final_status,
        summary=summary,
        config_version_id=config_version_id,
    )


async def _run_learning_step(
    session: AsyncSession,
    *,
    project: Project,
    run: SleepRun,
    current_snapshot: dict[str, Any],
    proposed_snapshot: dict[str, Any],
    per_agent_suggestions: dict[str, dict[str, Any]],
) -> LearningStepResult:
    """Drive the Q-update pass + atomic 3-write finalize for deep sleep.

    Splits the logic out of ``_execute_run`` so the orchestrator body
    stays readable. Returns the :class:`LearningStepResult` so the
    caller can stamp the summary string and decide whether to fire
    cache invalidation.

    Step ordering (per design #2070 and spec #2066):

    1. Pull every unconsumed episode since the previous sleep_run.
    2. Run the in-memory Q-update + special tagging pass.
    3. Compute the Q-Table risk class from (old_table, new_table) +
       project caps + TOP-K state walk — BEFORE the transaction so the
       class is known when we write the config_versions row.
    4. Finalize: q_tables INSERT + sleep_reports INSERT +
       config_versions INSERT + mark-special UPDATE inside one
       ``begin_nested`` savepoint.

    The synthesised reflection text + auditor metrics are folded into
    the sleep_report payload so the dashboard can render the run
    without joining sleep_reflections.
    """
    epi_repo = EpisodicMemoryRepository(session)
    cv_repo = ConfigVersionRepository(session)

    # 1+2. Pull episodes and run the pass. We use the project's
    # ``last_sleep_at`` as the "since" boundary — that's the timestamp
    # the orchestrator stamped at the END of the previous run.
    since = getattr(project, "last_sleep_at", None)
    pass_result = await apply_q_update_pass(
        session,
        user_id=project.user_id,
        project=project,
        sleep_run=run,
        since=since,
    )

    # 3. Top-K state list for the classifier's policy-implication walk.
    # An empty list signals cold-start and the classifier falls back to
    # the magnitude bracket.
    top_k = await epi_repo.top_k_states(
        user_id=project.user_id, project_id=project.id, k=20
    )

    from aether_api.learning.qtable_versioning import classify_qtable_delta

    risk_class = classify_qtable_delta(
        old_table=pass_result.old_table,
        new_table=pass_result.new_table,
        project=project,
        top_k_states=top_k,
    )

    # Snapshot the agent reflection digest into the sleep report. The
    # orchestrator already has ``per_agent_suggestions`` keyed by type;
    # we pass the worker bucket as ``worker_insights`` and the auditor
    # bucket as ``auditor_metrics`` so the dashboard reads the same
    # structure regardless of whether a Sleep Phase ran the learning
    # loop.
    worker_insights = per_agent_suggestions.get("worker") or {}
    auditor_metrics = per_agent_suggestions.get("auditor") or {}

    # ``improvements_applied``: which top-level keys changed between
    # current and proposed snapshot. The dashboard renders this as a
    # human-readable chip list.
    improvements_applied = diff_keys(
        current=current_snapshot, proposed=proposed_snapshot
    )

    # Parent for lineage — the latest applied version on this project.
    parent = await cv_repo.latest_applied_for_project(project.id)

    # 4. Atomic finalize. ``finalize_learning_step`` owns the savepoint;
    # an exception here propagates out of the orchestrator and the
    # outer ``session.commit()`` is never reached.
    return await finalize_learning_step(
        session,
        user_id=project.user_id,
        project=project,
        sleep_run=run,
        pass_result=pass_result,
        risk_class=risk_class,
        version_name=f"Auto-sleep {run.id}",
        parent_config_version_id=parent.id if parent is not None else None,
        auditor_metrics=auditor_metrics,
        worker_insights=worker_insights,
        improvements_applied=improvements_applied,
        summary_md=None,
        overall_score=0.0,
        proposed_snapshot=proposed_snapshot,
    )


__all__ = [
    "OrchestratorResult",
    "PHASE_TYPES",
    "run_sleep_phase",
]
