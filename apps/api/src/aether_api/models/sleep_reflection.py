"""``sleep_reflections`` table — one row per (sleep_run, agent_type).

The unique constraint enforces "at most one reflection per agent per
run" — retries / partial states overwrite rather than duplicate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Mirror of the DB CHECK on ``agent_type``.
#:
#: Migration 0011 extends this with ``'orchestrator'`` so the Orquestador
#: can land its own reflection alongside the other three (see
#: ``sleep-phase-delta`` spec for sleep-learning-loop). Order is kept
#: stable for golden-file equality in tests.
SLEEP_REFLECTION_AGENT_TYPES: Final[tuple[str, ...]] = (
    "orchestrator",
    "worker",
    "investigator",
    "auditor",
)


class SleepReflection(Base):
    """Reflection markdown + structured ``suggested_changes`` for one agent."""

    __tablename__ = "sleep_reflections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    sleep_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_type: Mapped[str] = mapped_column(String(20), nullable=False)

    reflection_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_changes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    # --- Relations
    sleep_run = relationship("SleepRun", back_populates="reflections", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "agent_type IN ('orchestrator', 'worker', 'investigator', 'auditor')",
            name="sleep_reflections_agent_type_valid",
        ),
        UniqueConstraint("sleep_run_id", "agent_type", name="uq_sleep_reflections_run_agent"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SleepReflection sleep_run_id={self.sleep_run_id} agent_type={self.agent_type}>"
