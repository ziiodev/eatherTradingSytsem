"""``agent_skills`` table — see migration ``0009_skills_markdown_and_agent_skills``.

Per-(agent, skill) binding with an optional ``notes`` field. CASCADE
from agents (deleting an agent removes its bindings); RESTRICT from
skills (cannot hard-delete a skill that is still attached). Multi-tenant
integrity is enforced at the application layer — the
:class:`aether_api.repositories.agent_skill_repository.AgentSkillRepository`
verifies that ``agent.user_id`` and ``skill.user_id`` both match
``current_user.id`` before INSERT.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class AgentSkill(Base):
    """One row per (agent_id, skill_id) binding.

    Storage shape mirrors migration ``0009`` byte-for-byte. The
    ``uq_agent_skills_pair`` UNIQUE constraint enforces the no-duplicate
    rule at the DB layer — the router maps the resulting IntegrityError
    to HTTP 409.
    """

    __tablename__ = "agent_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="RESTRICT"),
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )

    # Relationships — ``lazy='raise'`` is the project-wide convention so
    # joins must be explicit in the repository layer.
    agent = relationship("Agent", lazy="raise")
    skill = relationship("SkillDefinition", lazy="raise")

    __table_args__ = (
        UniqueConstraint("agent_id", "skill_id", name="uq_agent_skills_pair"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentSkill id={self.id} agent_id={self.agent_id} "
            f"skill_id={self.skill_id}>"
        )
