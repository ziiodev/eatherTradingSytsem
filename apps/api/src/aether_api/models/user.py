"""``users`` table — see CHARTER.md "Modelo de Datos: tabla `users`".

Every column here mirrors the migration 0001_init exactly. Order is
preserved to make side-by-side review obvious.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aether_api.db.base import Base


class User(Base):
    """Application user / tenant owner.

    Notes from the charter that future maintainers must respect:

    * ``email`` is enforced lowercase at the DB layer (CHECK constraint).
      Always normalise on insert with ``.lower()`` so the constraint
      never raises in application code.
    * ``password_hash`` is NULLABLE on purpose — OAuth users will not
      have one. Do not add NOT NULL without a migration plan.
    * ``mfa_secret_ref`` is a pointer to a secret store, NOT the secret.
    * ``failed_login_count`` + ``locked_until`` implement basic brute
      force throttling. Reset count to 0 on successful login.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # --- Identidad
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Credenciales
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Estado y roles
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    email_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- MFA (pre-wired, off by default)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    mfa_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Actividad
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Fechas
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    __table_args__ = (
        # Matches the named constraint in 0001_init: users_email_lower.
        CheckConstraint("email = LOWER(email)", name="users_email_lower"),
    )

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"<User id={self.id} email={self.email}>"
