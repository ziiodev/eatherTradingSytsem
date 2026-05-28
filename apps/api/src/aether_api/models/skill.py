"""``skills`` table — see ``sdd/skills-catalog/spec`` and migration ``0003_skills``.

The class is named ``SkillDefinition`` (not ``Skill``) so it does not
collide with the unrelated Claude/SDD "skills" concept in conversation,
while the UI still labels the surface "Skills" / "trading skills" per
the charter sidebar lock.

The ``type`` column stays a Python ``str`` here; the CHECK constraint
``skills_type_valid`` on the DB side is the source of truth for the
{indicator, data_source, analytic, executor, risk} set. A DB ENUM would
require a migration on every new skill type, which is exactly the
flexibility we don't want during the v1 catalog.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Canonical skill kinds. Kept here as a Python-side reference; the DB
#: CHECK constraint ``skills_type_valid`` is the source of truth.
SKILL_TYPES: Final[tuple[str, ...]] = (
    "indicator",
    "data_source",
    "analytic",
    "executor",
    "risk",
)

#: Canonical skill runtimes. Markdown is the default — skills are
#: knowledge artifacts (prompts, decision frameworks, entry/exit rules);
#: Python is reserved for computational/algorithmic capabilities
#: (indicators, correlation calculators, risk math). The DB CHECK
#: constraint ``skills_runtime_valid`` is the source of truth.
SKILL_RUNTIMES: Final[tuple[str, ...]] = ("markdown", "python")


class SkillDefinition(Base):
    """Reusable, user-scoped, named, versioned Python capability.

    Storage-only in v1: ``code`` is persisted as TEXT but never executed
    here — execution lands in the future ``agent-execution-sandbox`` change.
    """

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --- Identificacion
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Versionado
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # --- Cuerpo ejecutable y runtime
    code: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'markdown'")
    )

    # --- Firma tipada (TypedDict-like, NOT JSON Schema)
    input_signature: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_signature: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Estado
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # --- Fechas
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    # --- Relations
    user = relationship("User", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "type IN ('indicator', 'data_source', 'analytic', 'executor', 'risk')",
            name="skills_type_valid",
        ),
        CheckConstraint(
            "runtime IN ('markdown', 'python')",
            name="skills_runtime_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SkillDefinition id={self.id} type={self.type} name={self.name!r}>"
