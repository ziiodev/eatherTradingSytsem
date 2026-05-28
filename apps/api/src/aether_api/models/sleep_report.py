"""``sleep_reports`` table — 1:1 outcome digest for a sleep run.

Mirrors :file:`apps/api/alembic/versions/0011_sleep_learning_loop.py`
byte-for-byte semantically. Exactly one report per ``sleep_runs.id``
(UNIQUE NOT NULL FK with CASCADE). Multi-tenancy is enforced
transitively via ``sleep_runs.project_id → projects.user_id``.

The ``payload`` JSONB aggregates the structured outcome the dashboard
renders: Q-Table diff summary, episode counts ingested, semantic rule
diffs, the promoted ``config_versions.id`` (if any), and per-agent
reflection digests. ``summary_md`` is the operator-friendly
markdown digest.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class SleepReport(Base):
    """The structured outcome of one sleep run (1:1 with ``sleep_runs``)."""

    __tablename__ = "sleep_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    sleep_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )

    # --- Relations
    sleep_run = relationship("SleepRun", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SleepReport id={self.id} sleep_run_id={self.sleep_run_id}>"
