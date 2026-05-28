"""``mfa_recovery_codes`` table — argon2id-hashed single-use TOTP recovery codes.

Mirrors :file:`apps/api/alembic/versions/0005_mfa_recovery_codes.py` byte-
for-byte semantically. Any column added here MUST be reflected in the
migration and vice versa.

Single-use enforcement lives at the service layer
(:mod:`aether_api.services.mfa`) via an atomic
``UPDATE ... WHERE used_at IS NULL RETURNING id`` — the partial index on
``(user_id) WHERE used_at IS NULL`` keeps that scan O(unused).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aether_api.db.base import Base


class MfaRecoveryCode(Base):
    """One row per recovery code per user. Rotated atomically on regenerate."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # ON DELETE CASCADE mirrors the migration — losing the parent user nukes
    # the code list with no other dependencies to break.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("NOW()")
    )

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return (
            f"<MfaRecoveryCode id={self.id} user_id={self.user_id} "
            f"used={self.used_at is not None}>"
        )
