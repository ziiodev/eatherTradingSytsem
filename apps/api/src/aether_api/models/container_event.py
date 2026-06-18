"""``container_events`` table — per-project Docker lifecycle audit trail.

Written by every successful (and many failed) call inside
:mod:`aether_api.docker_control.lifecycle` plus the reconciler in
:mod:`aether_api.docker_control.reconcile`. The shape mirrors
``audit_log`` but is keyed on ``project_id`` (NOT NULL) so the project
detail page can render an events feed in O(1) on the composite index
``(project_id, created_at DESC)``.

Append-only is documented but NOT enforced by REVOKE on this table —
this is a domain audit, not a security-grade tamper-evident log. The
canonical security audit lives in ``audit_log`` (migration 0002), which
DOES have REVOKE UPDATE/DELETE applied at the role layer.

See migration ``0006_container_events`` for DDL and indexes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aether_api.db.base import Base


class ContainerEvent(Base):
    """One row per Docker lifecycle event observed by ``docker_control``.

    Fields:

    * ``project_id`` / ``user_id`` are NOT NULL (RESTRICT FKs). The
      router DELETE handler for projects already refuses to delete while
      ``container_id IS NOT NULL``; RESTRICT is the belt to that
      suspender at the DB layer.
    * ``action`` — free-form verb (e.g. ``"build"``, ``"create"``,
      ``"start"``, ``"pause"``, ``"unpause"``, ``"stop"``, ``"recreate"``,
      ``"remove"``, ``"reconcile_drift"``). Adding a new verb is a code
      change, not a migration.
    * ``status`` — free-form ``"ok"`` / ``"error"`` / ``"observed"``.
    * ``payload`` — JSONB envelope (caller PII-scrubs).
    * ``error`` — optional tail of the aiodocker / proxy error message.
    """

    __tablename__ = "container_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    pair_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pairs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return (
            f"<ContainerEvent id={self.id} pair={self.pair_id} "
            f"action={self.action!r} status={self.status!r}>"
        )
