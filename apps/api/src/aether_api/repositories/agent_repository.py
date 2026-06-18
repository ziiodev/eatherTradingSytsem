"""``agents`` data access — tenant-scoped."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from aether_api.models.agent import Agent
from aether_api.models.pair import Pair
from aether_api.repositories.base import BaseRepository


class AgentReferencedError(Exception):
    """Raised when an agent cannot be deleted because projects FK it.

    Routers catch this and surface a deterministic 409 with the referencing
    project IDs, instead of letting the SQLAlchemy ``IntegrityError``
    bubble out as an opaque 500.
    """

    def __init__(self, agent_id: uuid.UUID, project_ids: list[uuid.UUID]) -> None:
        super().__init__(
            f"agent {agent_id} is referenced by {len(project_ids)} project(s)"
        )
        self.agent_id = agent_id
        self.project_ids = project_ids


class AgentRepository(BaseRepository):
    model = Agent

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        type: str | None = None,  # noqa: A002 — matches API param
        is_active: bool | None = None,
    ) -> list[Agent]:
        stmt = self._for_user(select(Agent), user_id)
        if type is not None:
            stmt = stmt.where(Agent.type == type)
        if is_active is not None:
            stmt = stmt.where(Agent.is_active == is_active)
        stmt = stmt.order_by(Agent.updated_at.desc().nulls_last(), Agent.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self, user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Agent | None:
        """Return the agent IFF it belongs to ``user_id``, else ``None``.

        Same 404-not-403 contract as projects (see pair_repository).
        """
        stmt = self._for_user(select(Agent).where(Agent.id == agent_id), user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def projects_using_counts(
        self, user_id: uuid.UUID, agent_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Return ``{agent_id: count}`` for each ``agent_id`` in input.

        Counts how many of the caller's ``projects`` rows reference each
        agent via any of the six FK columns (orchestrator / investigator
        / marker / worker / tutor / auditor — migration 0012 added the
        Marker and Tutor slots). Used by the list endpoint so the UI
        can show a "referenced by N projects" badge — and so the delete
        endpoint can surface the same number in its 409 detail.
        """
        if not agent_ids:
            return {}

        # One row per (agent_id, project_id) pair where the project
        # references the agent in any of the six FK slots. Then
        # GROUP BY agent_id and COUNT(DISTINCT project_id).
        orchestrator_match = Pair.orchestrator_agent_id.in_(agent_ids)
        investigator_match = Pair.investigator_agent_id.in_(agent_ids)
        marker_match = Pair.marker_agent_id.in_(agent_ids)
        worker_match = Pair.worker_agent_id.in_(agent_ids)
        tutor_match = Pair.tutor_agent_id.in_(agent_ids)
        auditor_match = Pair.auditor_agent_id.in_(agent_ids)

        agent_id_expr = case(
            (
                Pair.orchestrator_agent_id.in_(agent_ids),
                Pair.orchestrator_agent_id,
            ),
            (
                Pair.investigator_agent_id.in_(agent_ids),
                Pair.investigator_agent_id,
            ),
            (
                Pair.marker_agent_id.in_(agent_ids),
                Pair.marker_agent_id,
            ),
            (Pair.worker_agent_id.in_(agent_ids), Pair.worker_agent_id),
            (Pair.tutor_agent_id.in_(agent_ids), Pair.tutor_agent_id),
            (Pair.auditor_agent_id.in_(agent_ids), Pair.auditor_agent_id),
        ).label("agent_id")

        stmt = (
            select(agent_id_expr, func.count(func.distinct(Pair.id)))
            .where(Pair.user_id == user_id)
            .where(
                or_(
                    orchestrator_match,
                    investigator_match,
                    marker_match,
                    worker_match,
                    tutor_match,
                    auditor_match,
                )
            )
            .group_by("agent_id")
        )
        result = await self.session.execute(stmt)
        counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in result.all() if row[0]}
        # Fill in zero counts for agents that have no referencing rows so
        # callers can index unconditionally.
        for aid in agent_ids:
            counts.setdefault(aid, 0)
        return counts

    async def list_referencing_projects(
        self, user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Return the ``id``s of the caller's projects that FK ``agent_id``.

        Scans all six FK slots — Orquestador, Investigador, Marker,
        Worker, Tutor, Auditor (migration 0012 added Marker + Tutor).
        """
        stmt = (
            select(Pair.id)
            .where(Pair.user_id == user_id)
            .where(
                or_(
                    Pair.orchestrator_agent_id == agent_id,
                    Pair.investigator_agent_id == agent_id,
                    Pair.marker_agent_id == agent_id,
                    Pair.worker_agent_id == agent_id,
                    Pair.tutor_agent_id == agent_id,
                    Pair.auditor_agent_id == agent_id,
                )
            )
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        type: str,  # noqa: A002 — matches column name
        logica: str,
        description: str | None = None,
        entrypoint: str | None = None,
    ) -> Agent:
        agent = Agent(
            user_id=user_id,
            name=name,
            type=type,
            logica=logica,
            description=description,
            entrypoint=entrypoint,
        )
        self.session.add(agent)
        await self.session.flush()
        await self.session.refresh(agent)
        return agent

    async def patch(
        self,
        agent: Agent,
        *,
        changes: dict[str, Any],
        bump_version: bool,
    ) -> Agent:
        """Apply ``changes`` to ``agent`` in-place. Caller has already done
        any cross-field validation; this method only writes.

        ``bump_version=True`` increments :attr:`Agent.version` by 1 —
        callers pass True iff ``logica`` actually changed (string equality
        check at the router boundary), per spec.
        """
        for field, value in changes.items():
            setattr(agent, field, value)
        if bump_version:
            agent.version = (agent.version or 1) + 1
        # ``updated_at`` is server-default NOW(); explicitly bump it so
        # the optimistic-locking precondition the next PATCH sees a fresh
        # timestamp.
        await self.session.flush()
        await self.session.refresh(agent)
        return agent

    async def archive(self, agent: Agent) -> Agent:
        """Soft-archive: set ``is_active = False``. Idempotent."""
        if agent.is_active:
            agent.is_active = False
            await self.session.flush()
            await self.session.refresh(agent)
        return agent

    async def delete(self, user_id: uuid.UUID, agent: Agent) -> None:
        """Hard delete. Raises :class:`AgentReferencedError` if any project
        of the caller references the agent via any FK slot.

        We check referenced-by FIRST so we can return a structured 409 with
        the project IDs; relying solely on the DB ``ON DELETE RESTRICT``
        would surface as an opaque ``IntegrityError`` with no detail.
        """
        referencing = await self.list_referencing_projects(user_id, agent.id)
        if referencing:
            raise AgentReferencedError(agent.id, referencing)

        try:
            await self.session.execute(delete(Agent).where(Agent.id == agent.id))
            await self.session.flush()
        except IntegrityError as exc:
            # Race: someone added a referencing project between our check
            # and the delete. Re-read the referencing list so the 409
            # detail stays accurate.
            await self.session.rollback()
            referencing = await self.list_referencing_projects(user_id, agent.id)
            raise AgentReferencedError(agent.id, referencing) from exc
