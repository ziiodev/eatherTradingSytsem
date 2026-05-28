"""``projects`` table — see CHARTER.md "Modelo de Datos: tabla `projects`".

A few design notes worth pinning:

* ``trading_sessions`` is stored as ``TEXT[]`` with a CHECK constraint
  on allowed values; SQLAlchemy maps that to ``ARRAY(String)``.
* JSONB columns (``*_params``) default to ``'{}'::jsonb`` server-side.
* ``status`` is a free-form ``VARCHAR(20)`` rather than a DB enum — the
  app validates membership in {active, paused, stopped, error, maintenance}.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import ARRAY, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base
from aether_api.services.project_lifecycle import PROJECT_STATUSES as _LIFECYCLE_STATUSES

#: Allowed values for ``status``. App-level membership check; DB does not
#: enforce this one (CHARTER.md leaves it free-form).
#:
#: This is intentionally a re-export of the canonical set defined in
#: :mod:`aether_api.services.project_lifecycle` — the lifecycle module is
#: the source of truth for both the status set and the transition matrix.
PROJECT_STATUSES: Final[tuple[str, ...]] = tuple(_LIFECYCLE_STATUSES)

#: Allowed values for elements of ``trading_sessions`` (DB CHECK enforces).
TRADING_SESSIONS: Final[tuple[str, ...]] = (
    "sydney",
    "shanghai",
    "tokyo",
    "europe",
    "new_york",
)


class Project(Base):
    """A single trading project (1:1:1:1 with Docker container / MT5 / MCP endpoint)."""

    __tablename__ = "projects"

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

    # --- Información básica
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'inactive'")
    )

    # --- Docker / Infraestructura
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    docker_image: Mapped[str | None] = mapped_column(
        String(100), server_default=text("'mt5-base:latest'")
    )
    mcp_url: Mapped[str] = mapped_column(String(255), nullable=False)
    mcp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Cuenta de trading
    account_login: Mapped[str | None] = mapped_column(String(50), nullable=True)
    account_server: Mapped[str | None] = mapped_column(String(100), nullable=True)
    broker_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    account_leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Costes / Comisiones
    commission_per_lot: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    commission_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    swap_long: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    swap_short: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    spread_typical: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    # --- Configuración de riesgo
    capital_asignado: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    risk_per_trade: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), server_default=text("1.0")
    )
    max_daily_dd: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default=text("3.0"))
    max_total_dd: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default=text("8.0"))
    max_exposure: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default=text("10.0"))

    # --- Estrategia
    strategy_version: Mapped[int | None] = mapped_column(Integer, server_default=text("1"))
    strategy_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_logic: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Vinculación a agentes
    # Charter correction (migration 0010): the Orquestador is now a
    # first-class FK like the other three (Worker / Investigador /
    # Auditor). Existing rows keep ``orchestrator_agent_id = NULL``
    # until the operator wires one in the dashboard.
    orchestrator_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    worker_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    investigator_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    auditor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # --- Ventanas operativas
    trading_sessions: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    # --- Parámetros por agente (JSONB libre)
    orchestrator_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    auditor_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    investigator_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    worker_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Fechas y control
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    last_active_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    last_sleep_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Metadata
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Relations
    user = relationship("User", lazy="raise")
    orchestrator_agent = relationship(
        "Agent", foreign_keys=[orchestrator_agent_id], lazy="raise"
    )
    worker_agent = relationship("Agent", foreign_keys=[worker_agent_id], lazy="raise")
    investigator_agent = relationship("Agent", foreign_keys=[investigator_agent_id], lazy="raise")
    auditor_agent = relationship("Agent", foreign_keys=[auditor_agent_id], lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name={self.name!r} status={self.status}>"
