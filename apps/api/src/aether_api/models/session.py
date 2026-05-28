"""``sessions`` table — see CHARTER.md "Modelo de Datos: tabla `sessions`".

Important naming note: the ORM class is :class:`UserSession`, NOT
``Session`` — that would shadow SQLAlchemy's own ``Session``. The
``__tablename__`` is still ``sessions``, matching the migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import INET, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class UserSession(Base):
    """One row per active refresh-token-backed session.

    Hard rules:

    * ``refresh_token_hash`` stores the SHA-256 hex of the opaque refresh
      token. The raw token is NEVER persisted.
    * Revocation is *soft* (``revoked_at = NOW()``) — rows are kept for
      audit. A nightly job MAY purge rows where ``revoked_at < NOW() -
      90 days`` but that is out of scope here.
    * ``ON DELETE CASCADE`` from users (vs RESTRICT elsewhere) — when a
      user is genuinely deleted their sessions follow them out.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Token
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # --- Contexto del cliente
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Lifecycle
    issued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Relations
    user = relationship("User", lazy="raise")

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="sessions_expires_after_issued"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="sessions_revoked_after_issued",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        revoked = self.revoked_at is not None
        return f"<UserSession id={self.id} user_id={self.user_id} revoked={revoked}>"
