"""``eas`` table — Expert Advisor artifacts (see ``sdd/ea-management``).

Standalone, user-scoped, reusable, versioned, soft-archived artifact whose
body is the serialized React Flow graph (``{nodes, edges}`` envelope stored as
JSONB). Modelled on the existing ``skills`` domain — the closest analogue in
the codebase (user-scoped, reusable, ``version`` int, ``is_active`` soft
archive, ``_for_user`` tenant primitive, 404-not-403 cross-tenant denial).

The migration that creates this table is ``0002_eas`` (chained off the squashed
``0001_init``). newTCN's hidden ``projects`` container is intentionally NOT
ported — EAs are flat and directly owned by a user.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class EA(Base):
    """One row = one user-authored Expert Advisor.

    The ``graph`` column holds the serialized React Flow graph as the
    ``{nodes, edges}`` envelope. A fresh row defaults to an empty, valid graph
    (mirrors the DDL default) so the editor never has to special-case a
    NULL/absent body. Codegen (``services/codegen``) is a pure function over
    this dict — the model never executes it.
    """

    __tablename__ = "eas"

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

    # --- Identificacion
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Cuerpo: grafo React Flow serializado ({nodes, edges})
    graph: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{\"nodes\": [], \"edges\": []}'::jsonb"),
    )

    # --- Versionado
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # --- Estado (soft-archive)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # --- Fechas
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    # --- Relations
    user = relationship("User", lazy="raise")

    __table_args__ = (
        CheckConstraint("version >= 1", name="eas_version_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EA id={self.id} name={self.name!r} version={self.version}>"
