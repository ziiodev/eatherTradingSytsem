"""``sleep_runs`` table — one row per Micro / Profundo / Crítico sleep run.

Mirrors :file:`apps/api/alembic/versions/0008_sleep_phase.py` byte-for-byte
semantically (column types, nullability, defaults, CHECK constraints).
Any divergence is a bug — fix the model OR the migration, never patch
around it in the orchestrator.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Closed enum of phase flavours. The DB CHECK constraint is the source of truth.
SLEEP_PHASE_TYPES: Final[tuple[str, ...]] = ("micro", "profundo", "critico")

#: Closed enum of run statuses.
#: - running:    in-flight (only legal NULL ``ended_at``).
#: - succeeded:  workflow completed end-to-end.
#: - failed:     orchestrator-level failure (e.g. sandbox-disabled).
#: - crashed:    process died mid-flight; surfaced by the boot sweep.
#: - skipped:    project status disallowed running (e.g. 'stopped').
#: - partial:    one or more agent reflections failed but the run finished.
SLEEP_RUN_STATUSES: Final[tuple[str, ...]] = (
    "running",
    "succeeded",
    "failed",
    "crashed",
    "skipped",
    "partial",
)


class SleepRun(Base):
    """One row per orchestrator invocation."""

    __tablename__ = "sleep_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    phase_type: Mapped[str] = mapped_column(String(20), nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Relations
    project = relationship("Project", lazy="raise")
    user = relationship("User", lazy="raise")
    reflections = relationship(
        "SleepReflection",
        back_populates="sleep_run",
        lazy="raise",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "phase_type IN ('micro', 'profundo', 'critico')",
            name="sleep_runs_phase_type_valid",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','crashed','skipped','partial')",
            name="sleep_runs_status_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SleepRun id={self.id} project_id={self.project_id} "
            f"phase={self.phase_type} status={self.status}>"
        )
