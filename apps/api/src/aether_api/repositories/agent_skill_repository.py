"""``agent_skills`` data access.

Bindings are owned (transitively) by a single user: both ``agent.user_id``
and ``skill.user_id`` must match ``current_user.id`` before INSERT. Cross-
tenant attempts return a typed exception that the router maps to 404 —
same non-disclosure contract as the rest of the resource graph.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from aether_api.models.agent import Agent
from aether_api.models.agent_skill import AgentSkill
from aether_api.models.skill import SkillDefinition
from aether_api.repositories.base import BaseRepository


class AgentSkillTenancyError(Exception):
    """Raised when the requested skill is not owned by the same user as
    the requested agent (or one of them does not exist for the caller).

    Routers translate this into HTTP 404 — same non-disclosure contract as
    the cross-tenant 404 used everywhere else in the API.
    """


class AgentSkillAlreadyAttachedError(Exception):
    """Raised when an attach call would duplicate an existing binding.

    Mapped to HTTP 409 by the router.
    """

    def __init__(self, agent_id: uuid.UUID, skill_id: uuid.UUID) -> None:
        super().__init__(
            f"skill {skill_id} is already attached to agent {agent_id}"
        )
        self.agent_id = agent_id
        self.skill_id = skill_id


class AgentSkillRepository(BaseRepository):
    """CRUD for ``agent_skills`` with explicit tenancy gating."""

    model = AgentSkill

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_agent(
        self, user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[tuple[AgentSkill, SkillDefinition]]:
        """Return every (binding, skill) pair attached to ``agent_id``.

        Caller MUST have already verified that ``agent_id`` belongs to
        ``user_id`` (the agents repository does this). We re-apply the
        tenancy filter at the join level too — defence-in-depth, so a
        forgotten upstream check still cannot leak rows.
        """
        stmt = (
            select(AgentSkill, SkillDefinition)
            .join(SkillDefinition, AgentSkill.skill_id == SkillDefinition.id)
            .join(Agent, AgentSkill.agent_id == Agent.id)
            .where(AgentSkill.agent_id == agent_id)
            .where(Agent.user_id == user_id)
            .where(SkillDefinition.user_id == user_id)
            .order_by(AgentSkill.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_pair(
        self,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
    ) -> AgentSkill | None:
        """Return the binding row for (agent_id, skill_id) if the caller
        owns both endpoints, else ``None``.
        """
        stmt = (
            select(AgentSkill)
            .join(Agent, AgentSkill.agent_id == Agent.id)
            .join(
                SkillDefinition, AgentSkill.skill_id == SkillDefinition.id
            )
            .where(AgentSkill.agent_id == agent_id)
            .where(AgentSkill.skill_id == skill_id)
            .where(Agent.user_id == user_id)
            .where(SkillDefinition.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_for_skill(
        self, user_id: uuid.UUID, skill_id: uuid.UUID
    ) -> int:
        """Return how many of the caller's agents reference ``skill_id``.

        Filtered by both endpoints to ``user_id`` so the count never
        leaks bindings from other tenants.
        """
        stmt = (
            select(func.count(AgentSkill.id))
            .join(Agent, AgentSkill.agent_id == Agent.id)
            .join(
                SkillDefinition, AgentSkill.skill_id == SkillDefinition.id
            )
            .where(AgentSkill.skill_id == skill_id)
            .where(Agent.user_id == user_id)
            .where(SkillDefinition.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def attach(
        self,
        *,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        notes: str | None = None,
    ) -> AgentSkill:
        """Insert a (agent_id, skill_id) binding.

        Verifies that BOTH endpoints belong to ``user_id`` before INSERT.
        Raises :class:`AgentSkillTenancyError` if either endpoint is
        missing or owned by another tenant — the router maps this to a
        404 so cross-tenant existence is not disclosed.

        Raises :class:`AgentSkillAlreadyAttachedError` (mapped to 409)
        on duplicate via the UNIQUE constraint.
        """
        agent_owned = await self.session.execute(
            select(Agent.id).where(Agent.id == agent_id).where(Agent.user_id == user_id)
        )
        if agent_owned.scalar_one_or_none() is None:
            raise AgentSkillTenancyError(
                f"agent {agent_id} not owned by user {user_id}"
            )
        skill_owned = await self.session.execute(
            select(SkillDefinition.id)
            .where(SkillDefinition.id == skill_id)
            .where(SkillDefinition.user_id == user_id)
        )
        if skill_owned.scalar_one_or_none() is None:
            raise AgentSkillTenancyError(
                f"skill {skill_id} not owned by user {user_id}"
            )

        binding = AgentSkill(
            agent_id=agent_id,
            skill_id=skill_id,
            notes=notes,
        )
        self.session.add(binding)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AgentSkillAlreadyAttachedError(agent_id, skill_id) from exc
        await self.session.refresh(binding)
        return binding

    async def detach(
        self,
        *,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
    ) -> int:
        """Delete the (agent_id, skill_id) binding. Returns affected row count.

        We first call :meth:`get_pair` to enforce tenancy before issuing
        the DELETE; that lookup tells us whether ANY row exists for the
        caller. The follow-up DELETE is unconditional but bounded by the
        composite key, so it can only ever affect 0 or 1 rows.
        """
        existing = await self.get_pair(user_id, agent_id, skill_id)
        if existing is None:
            return 0
        await self.session.execute(
            delete(AgentSkill)
            .where(AgentSkill.agent_id == agent_id)
            .where(AgentSkill.skill_id == skill_id)
        )
        await self.session.flush()
        return 1
