"""``config_versions`` table — append-only project-config snapshots.

Lineage is captured by the self-FK ``parent_version_id``: a revert
appends a NEW row whose parent points at the version being undone.
This keeps the history a strict DAG (in practice a list) — we never
mutate or delete a prior version.

``status`` lifecycle::

    pending → approved → applied
            ↘ rejected
            ↘ reverted   (only reachable as a parent's status when a
                          newer version supersedes it)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Closed enums — DB CHECK constraints are the source of truth.
CONFIG_VERSION_RISK_CLASSES: Final[tuple[str, ...]] = ("bajo", "medio", "alto")
CONFIG_VERSION_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "approved",
    "rejected",
    "applied",
    "reverted",
)


class ConfigVersion(Base):
    """One append-only snapshot of a project's mutable configuration."""

    __tablename__ = "config_versions"

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
    # Self-FK lineage. NULL on the first version of a project.
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("config_versions.id"),
        nullable=True,
    )
    # NULL for hand-edited / out-of-band snapshots (not produced today
    # but the schema admits the case).
    sleep_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id"),
        nullable=True,
    )

    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    risk_class: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    proposed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Sleep-learning-loop columns (migration 0011)
    # Canonical reference to the ``q_tables`` snapshot this config version
    # pinned at promotion time (e.g. ``"v42"``). NULL for pre-0011 rows
    # and for snapshots authored outside the learning loop.
    q_table_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Frozen agent prompt(s) at promotion time so reverts roll back
    # behaviour, not just numbers.
    prompt_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Operator-friendly label rendered by the dashboard.
    version_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # --- Relations
    project = relationship("Project", lazy="raise")
    sleep_run = relationship("SleepRun", lazy="raise")
    parent = relationship(
        "ConfigVersion",
        remote_side="ConfigVersion.id",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint(
            "risk_class IN ('bajo', 'medio', 'alto')",
            name="config_versions_risk_class_valid",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','reverted')",
            name="config_versions_status_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ConfigVersion id={self.id} project_id={self.project_id} "
            f"risk={self.risk_class} status={self.status}>"
        )
