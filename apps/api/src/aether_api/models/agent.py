"""``agents`` table — see CHARTER.md "Modelo de Datos: tabla `agents`".

Note: ``type`` is a Python ``str`` here; the CHECK constraint on the DB
side enforces the {worker, investigator, auditor} set. A DB ENUM would
require a migration on every new agent type which is exactly the
flexibility we don't want.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Canonical agent kinds. Kept here as a Python-side reference; the DB
#: CHECK constraint ``agents_type_valid`` is the source of truth.
AGENT_TYPES: Final[tuple[str, ...]] = ("worker", "investigator", "auditor")


class Agent(Base):
    """Reusable agent definition (Worker / Investigator / Auditor).

    The Orchestrator is intentionally NOT modeled here — it is the
    backend's control plane, not a user-defined object.
    """

    __tablename__ = "agents"

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

    # --- Identificación
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Lógica ejecutable
    logica: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'python'")
    )
    entrypoint: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- Versionado y estado
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # --- Fechas
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    # --- Relations
    user = relationship("User", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "type IN ('worker', 'investigator', 'auditor')",
            name="agents_type_valid",
        ),
        CheckConstraint("runtime = 'python'", name="agents_runtime_only_python"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Agent id={self.id} type={self.type} name={self.name!r}>"
