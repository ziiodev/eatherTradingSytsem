"""``pairs`` table — a single trading pair (renamed from ``projects``).

In the accounts-pairs hierarchy ``Exchange → Account → Pair → Agents`` a
``Pair`` is the runtime unit: 1 pair = 1 Docker container = 1 MT5 instance
= 1 MCP endpoint. It was formerly the ``projects`` table; the rename +
reparent applied versus the pre-squash schema:

* the 7 broker-credential columns were LIFTED OFF onto :class:`Account`;
* ``account_id`` (FK RESTRICT, NOT NULL) was ADDED;
* the denormalised ``user_id NOT NULL`` is KEPT (one-hop ``_for_user``).

Mirrors :file:`apps/api/alembic/versions/0001_init.py` byte-for-byte
semantically.

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
from aether_api.services.pair_lifecycle import PAIR_STATUSES as _LIFECYCLE_STATUSES

#: Allowed values for ``status``. App-level membership check; DB does not
#: enforce this one (CHARTER.md leaves it free-form).
#:
#: This is intentionally a re-export of the canonical set defined in
#: :mod:`aether_api.services.pair_lifecycle` — the lifecycle module is
#: the source of truth for both the status set and the transition matrix.
PAIR_STATUSES: Final[tuple[str, ...]] = tuple(_LIFECYCLE_STATUSES)

#: Allowed values for elements of ``trading_sessions`` (DB CHECK enforces).
TRADING_SESSIONS: Final[tuple[str, ...]] = (
    "sydney",
    "shanghai",
    "tokyo",
    "europe",
    "new_york",
)


class Pair(Base):
    """A single trading pair (1:1:1:1 with Docker container / MT5 / MCP endpoint)."""

    __tablename__ = "pairs"

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
    # --- Reparent: every pair belongs to exactly one account (RESTRICT).
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
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
    # Charter corrections:
    #   * Migration 0010 — the Orquestador is a first-class FK like
    #     Worker / Investigador / Auditor. Existing rows keep
    #     ``orchestrator_agent_id = NULL`` until the operator wires one.
    #   * Migration 0012 — adds ``marker_agent_id`` (market-signal
    #     agent, split from the prior Investigador role) and
    #     ``tutor_agent_id`` (Sleep Phase conductor, split from the
    #     Orquestador's responsibilities). Both default to NULL on
    #     existing rows.
    orchestrator_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    investigator_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    marker_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    worker_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    tutor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
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
    investigator_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    marker_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    worker_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tutor_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    auditor_params: Mapped[dict[str, Any]] = mapped_column(
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
    account = relationship(
        "Account",
        foreign_keys=[account_id],
        back_populates="pairs",
        lazy="raise",
    )
    orchestrator_agent = relationship(
        "Agent", foreign_keys=[orchestrator_agent_id], lazy="raise"
    )
    investigator_agent = relationship(
        "Agent", foreign_keys=[investigator_agent_id], lazy="raise"
    )
    marker_agent = relationship(
        "Agent", foreign_keys=[marker_agent_id], lazy="raise"
    )
    worker_agent = relationship("Agent", foreign_keys=[worker_agent_id], lazy="raise")
    tutor_agent = relationship(
        "Agent", foreign_keys=[tutor_agent_id], lazy="raise"
    )
    auditor_agent = relationship("Agent", foreign_keys=[auditor_agent_id], lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Pair id={self.id} name={self.name!r} status={self.status}>"


__all__ = ["PAIR_STATUSES", "TRADING_SESSIONS", "Pair"]
