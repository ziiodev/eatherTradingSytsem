"""``agent_runs`` table — append-only audit of every sandbox invocation.

Mirrors :file:`apps/api/alembic/versions/0004_agent_runs.py` byte-for-byte
semantically (column types, nullability, defaults, CHECK constraints). Any
divergence is a bug — fix the model OR the migration, NEVER patch around
it in the repository layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Closed enum of statuses the sandbox engine produces. The DB CHECK
#: constraint ``agent_runs_status_valid`` is the source of truth.
AGENT_RUN_STATUSES: Final[tuple[str, ...]] = (
    "running",
    "success",
    "denied_import",
    "denied_network",
    "denied_file",
    "timeout",
    "oom",
    "error",
)


class AgentRun(Base):
    """One row per :func:`aether_api.sandbox.engine.Engine.run_agent` call.

    INSERT is performed BEFORE spawning the child (status='running' + NULL
    ``ended_at``); the row is UPDATEd on completion. The
    ``agent_runs_running_no_ended`` CHECK enforces the lifecycle invariant
    at the DB layer.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # --- Audit anchors (RESTRICT on all three).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --- Lifecycle.
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Outcome.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Relations (lazy='raise' is the project-wide convention — joins
    # MUST be explicit in the repository layer).
    user = relationship("User", lazy="raise")
    agent = relationship("Agent", lazy="raise")
    project = relationship("Project", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'running','success','denied_import','denied_network',"
            "'denied_file','timeout','oom','error'"
            ")",
            name="agent_runs_status_valid",
        ),
        CheckConstraint(
            "(status = 'running' AND ended_at IS NULL)"
            " OR (status <> 'running' AND ended_at IS NOT NULL)",
            name="agent_runs_running_no_ended",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentRun id={self.id} agent_id={self.agent_id} "
            f"status={self.status}>"
        )
