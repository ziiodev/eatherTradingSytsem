"""``agents`` table — see CHARTER.md "Modelo de Datos: tabla `agents`".

Note: ``type`` is a Python ``str`` here; the CHECK constraint on the DB
side enforces the set {orchestrator, investigator, marker, worker,
tutor, auditor}. A DB ENUM would require a migration on every new
agent type which is exactly the flexibility we don't want.

Charter corrections:
  * Migration 0010 — the Orquestador IS a definable agent like the
    others. Previous wording that treated it as the backend control
    plane only was wrong.
  * Migration 0012 — the Investigador is re-scoped to **news only**;
    the prior market-signal duty moves to the new ``marker`` agent.
    A new ``tutor`` agent now owns the Sleep Phase mechanics.
    Existing ``investigator`` rows are NOT mutated — operators re-tag
    by hand if they want a row to mean "marker" rather than
    "news watcher".
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
#:
#: Convention for ``entrypoint`` per type:
#:   * ``orchestrator`` → ``orchestrate(ctx)`` — supervises every other
#:     agent and decides which to dispatch.
#:   * ``investigator`` → ``analyze_news(ctx)`` — reads and summarises
#:     all relevant news sources. (Pre-0012 rows that use
#:     ``analyze(ctx)`` keep working — that name remains a documented
#:     soft fallback for legacy rows.)
#:   * ``marker``       → ``mark_signal(ctx)`` — emits the current
#:     market regime + the option to switch on. New in 0012.
#:   * ``worker``       → ``on_tick(ctx)``    — executes per-tick
#:     trading against MT5 via MCP.
#:   * ``tutor``        → ``on_sleep(ctx)``   — conducts the Sleep
#:     Phase (Micro / Profundo / Crítico). New in 0012.
#:   * ``auditor``      → ``evaluate(ctx)``   — checks risk + plan
#:     adherence; scope expanded in 0012 to also analyse the q-table
#:     and the MT5 broker reports.
#:
#: Order matches the charter prose: supervisor → research news →
#: market signal → execute → sleep/teach → audit.
AGENT_TYPES: Final[tuple[str, ...]] = (
    "orchestrator",
    "investigator",
    "marker",
    "worker",
    "tutor",
    "auditor",
)


class Agent(Base):
    """Reusable agent definition.

    Six types (post-0012):

    * ``orchestrator`` — system supervisor (unchanged).
    * ``investigator`` — news watcher (re-scoped in 0012).
    * ``marker`` — market-signal + option-to-switch (new in 0012).
    * ``worker`` — bot-style execution against MT5/MCP (unchanged).
    * ``tutor`` — Sleep Phase conductor (new in 0012).
    * ``auditor`` — operativa + q-table + MT5 reports (scope expanded
      in 0012; no DDL change).
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
            "type IN ('orchestrator', 'investigator', 'marker', "
            "'worker', 'tutor', 'auditor')",
            name="agents_type_valid",
        ),
        CheckConstraint("runtime = 'python'", name="agents_runtime_only_python"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Agent id={self.id} type={self.type} name={self.name!r}>"
