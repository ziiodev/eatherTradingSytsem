"""``audit_log`` table — append-only record of state-changing actions.

Why a dedicated table (not "just structured logs"):

* The charter mandates a hard audit trail. Logs may be sampled, dropped,
  or shipped to an aggregator that operators can mass-delete; a DB row
  with REVOKEd UPDATE/DELETE grants cannot be silently rewritten.
* Logs are unbounded text and useless for "show me what happened on
  project X yesterday" queries; the indexed columns here support fast
  per-user / per-action lookups.

Append-only is enforced at the **DB grant** level — see migration
0002_audit_log: at the end of upgrade we ``GRANT INSERT, SELECT`` and
explicitly ``REVOKE UPDATE, DELETE`` from the application role. The ORM
side mirrors this by exposing no mutating helpers on the repository.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aether_api.db.base import Base


class AuditLog(Base):
    """One row per audited mutation.

    Fields:

    * ``user_id`` is NULLABLE on purpose — system actions (sleep-phase
      reflection, scheduled jobs) have no caller, but still want a row.
    * ``before_state`` / ``after_state`` carry JSONB snapshots. The caller
      MUST scrub PII before passing values; the repository helper does NOT
      re-scrub because the same fields will be redacted at the log layer
      via :func:`aether_api.core.pii.scrub_mapping`.
    * ``ip_address`` and ``user_agent`` are taken from the request and
      may be NULL for non-HTTP entrypoints.
    * ``ON DELETE RESTRICT`` from users — if you can't delete a user that
      owns projects, you really can't delete a user that has audit rows.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"target={self.target_type}:{self.target_id}>"
        )
