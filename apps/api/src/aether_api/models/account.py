"""``accounts`` table — grouping layer between Exchange and Pair.

NEW in the accounts-pairs hierarchy: ``Exchange → Account → Pair → Agents``.
Mirrors :file:`apps/api/alembic/versions/0001_init.py` byte-for-byte
semantically (column types, nullability, defaults). Any divergence is a
bug — fix the model OR the migration, never patch around it in the
repository layer.

The ``accounts`` table OWNS the broker-credential block that used to live
on ``projects`` (now ``pairs``). Every pair under an account INHERITS
these credentials — there is no per-pair override. ``account_credential_ref``
is ALWAYS a pointer into an external secret store, never a plaintext
password (CHARTER hard rule).

Both FKs (``user_id``, ``exchange_id``) are ``ON DELETE RESTRICT``: an
account groups live trading pairs, so neither its owner nor its venue can
be hard-deleted while it exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class Account(Base):
    """A broker/exchange account owned by a tenant, scoped to one exchange."""

    __tablename__ = "accounts"

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
    exchange_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exchanges.id", ondelete="RESTRICT"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Broker-credential block (MOVED here off the old projects table).
    # account_credential_ref is ALWAYS a secret-store pointer, never plaintext.
    account_login: Mapped[str | None] = mapped_column(String(50), nullable=True)
    account_server: Mapped[str | None] = mapped_column(String(100), nullable=True)
    broker_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    account_leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

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
    exchange = relationship(
        "Exchange",
        back_populates="accounts",
        lazy="raise",
    )
    pairs = relationship(
        "Pair",
        back_populates="account",
        lazy="raise",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Account id={self.id} name={self.name!r} exchange_id={self.exchange_id}>"


__all__ = ["Account"]
