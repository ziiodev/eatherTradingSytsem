"""``exchanges`` table — first-class trading venue, user-scoped.

NEW in the accounts-pairs hierarchy: ``Exchange → Account → Pair → Agents``.
Mirrors :file:`apps/api/alembic/versions/0001_init.py` byte-for-byte
semantically (column types, nullability, defaults, CHECK constraints).
Any divergence is a bug — fix the model OR the migration, never patch
around it in the repository layer.

An exchange is the top of the hierarchy: it groups the broker/exchange
accounts a single operator holds at a given venue. ``code`` is unique per
tenant (``uq_exchanges_user_code``) so an operator cannot register the
same venue code twice; ``kind`` is CHECK-constrained to
``broker | exchange | prop | demo``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Allowed values for ``kind``. DB CHECK ``exchanges_kind_valid`` is the
#: source of truth.
EXCHANGE_KINDS: Final[tuple[str, ...]] = ("broker", "exchange", "prop", "demo")


class Exchange(Base):
    """A trading venue owned by a single tenant."""

    __tablename__ = "exchanges"

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

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'broker'")
    )

    meta_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )

    # --- Relations (lazy='raise' is the project-wide convention).
    user = relationship("User", lazy="raise")
    accounts = relationship(
        "Account",
        back_populates="exchange",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('broker', 'exchange', 'prop', 'demo')",
            name="exchanges_kind_valid",
        ),
        UniqueConstraint("user_id", "code", name="uq_exchanges_user_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Exchange id={self.id} code={self.code!r} kind={self.kind}>"


__all__ = ["EXCHANGE_KINDS", "Exchange"]
