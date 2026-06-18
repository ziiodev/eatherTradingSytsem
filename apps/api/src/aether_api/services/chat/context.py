"""Chat dispatch context + system prompt assembly.

The :class:`ChatDispatchContext` is a frozen dataclass carrying the
identity, the persistence factory and the LLM client into every tool
call. It is built ONCE per HTTP request by the router (after the
``current_user`` dependency has resolved) and threaded immutably into
the streaming generator.

System prompt layout — TWO blocks (see ``sdd/project-chat/design`` and
``sdd/project-chat/spec/chat`` for the rationale):

* **Block 1 — static** carries the role declaration, the tool
  catalogue, and the Aether trading-system domain context. It is
  invariant across requests for a given tenant and ships with
  ``cache_control={"type": "ephemeral"}`` so Anthropic's prompt-cache
  feature keeps the input-token cost low for repeat turns within the
  same conversation.
* **Block 2 — dynamic** carries the per-project snapshot (status, last
  sleep report, active rules, Q-Table version). It MUST NOT carry
  ``cache_control`` — the snapshot changes on every turn and caching
  it would re-use stale state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.pair import Pair
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.semantic_memory_repository import (
    SemanticMemoryRepository,
)
from aether_api.repositories.sleep_report_repository import SleepReportRepository

#: Default upper bound on assistant→tool→assistant round-trips per turn.
#: A 6th tool_use during a turn surfaces ``TOOL_ROUNDTRIP_LIMIT`` on the
#: SSE stream and persists a partial assistant row.
DEFAULT_MAX_TOOL_ROUNDTRIPS: int = 5


@dataclass(frozen=True, slots=True)
class ChatDispatchContext:
    """Frozen carrier of tenancy + persistence + LLM client.

    ``user_id`` / ``pair_id`` / ``conversation_id`` come from the
    authenticated request and are NEVER overridden by anything the
    LLM emits — every tool sanitises its input to drop those keys
    before calling the underlying repository.

    ``db_session_factory`` is an ``async`` callable that yields a
    fresh :class:`AsyncSession`. Tools open + close a short-lived
    session each call so a tool failure cannot poison the long-lived
    streaming transaction.

    ``llm_client`` is ``Any`` because the chat service may run against
    the real ``anthropic.Anthropic`` or a fake injected by tests. The
    consumer (the stream module) is the only thing that touches it.
    """

    user_id: uuid.UUID
    pair_id: uuid.UUID
    conversation_id: uuid.UUID
    db_session_factory: Callable[[], Any]
    llm_client: Any
    max_tool_roundtrips: int = DEFAULT_MAX_TOOL_ROUNDTRIPS
    #: Free-form per-request metadata (e.g. the resolved model name). The
    #: router populates this so tools / stream events can echo it back.
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pair snapshot — feeds the dynamic system-prompt block.
# ---------------------------------------------------------------------------


async def build_pair_snapshot(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    pair_id: uuid.UUID,
) -> dict[str, Any]:
    """Assemble a JSON-serialisable snapshot of the pair's current state.

    Returns a neutral payload when the caller does not own ``pair_id`` —
    cross-tenant probes do not disclose existence.

    Fields:

    * ``pair`` — name / symbol / timeframe / status / risk knobs.
      ``None`` when the pair does not exist or is cross-tenant.
    * ``latest_sleep_report`` — ``{sleep_run_id, summary, overall_score,
      generated_at}`` or ``None``.
    * ``active_rules_count`` — int. Number of ``semantic_memory`` rows
      with ``active = true`` for the pair.
    * ``q_table_version`` — int or ``None``. Highest version on
      ``q_tables`` for the pair.
    * ``generated_at`` — ISO-8601 stamp; useful for the LLM to reason
      about how fresh the snapshot is.
    """
    pair_stmt = select(Pair).where(
        Pair.id == pair_id, Pair.user_id == user_id
    )
    proj_row = (await session.execute(pair_stmt)).scalar_one_or_none()

    if proj_row is None:
        return {
            "pair": None,
            "latest_sleep_report": None,
            "active_rules_count": 0,
            "q_table_version": None,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    sleep_repo = SleepReportRepository(session)
    # Find the freshest sleep_run for this pair (any status) and
    # fetch its report, if one exists. ``SleepReportRepository`` only
    # exposes ``get_by_run_id``, so do the run lookup directly.
    from aether_api.models.sleep_run import SleepRun  # local import (cyclic-avoid)

    run_stmt = (
        select(SleepRun.id, SleepRun.ended_at, SleepRun.started_at)
        .where(SleepRun.pair_id == pair_id)
        .order_by(
            SleepRun.ended_at.desc().nulls_last(),
            SleepRun.started_at.desc().nulls_last(),
        )
        .limit(1)
    )
    latest_run = (await session.execute(run_stmt)).first()

    latest_sleep_report: dict[str, Any] | None = None
    if latest_run is not None:
        run_id, ended_at, started_at = latest_run
        report = await sleep_repo.get_by_run_id(
            user_id=user_id, sleep_run_id=run_id
        )
        if report is not None:
            payload = report.payload or {}
            generated_at_dt = ended_at or started_at
            latest_sleep_report = {
                "sleep_run_id": str(run_id),
                "summary": report.summary_md,
                "overall_score": payload.get("overall_score"),
                "generated_at": (
                    generated_at_dt.isoformat() if generated_at_dt else None
                ),
            }

    sem_repo = SemanticMemoryRepository(session)
    active_rules = await sem_repo.list_active(
        user_id=user_id, project_id=pair_id
    )

    q_repo = QTableRepository(session)
    latest_q = await q_repo.get_latest(user_id=user_id, project_id=pair_id)

    return {
        "pair": {
            "id": str(proj_row.id),
            "name": proj_row.name,
            "symbol": proj_row.symbol,
            "timeframe": proj_row.timeframe,
            "status": proj_row.status,
            "risk_per_trade": (
                float(proj_row.risk_per_trade)
                if proj_row.risk_per_trade is not None
                else None
            ),
            "max_daily_dd": (
                float(proj_row.max_daily_dd)
                if proj_row.max_daily_dd is not None
                else None
            ),
            "max_total_dd": (
                float(proj_row.max_total_dd)
                if proj_row.max_total_dd is not None
                else None
            ),
            "max_exposure": (
                float(proj_row.max_exposure)
                if proj_row.max_exposure is not None
                else None
            ),
            "capital_asignado": (
                float(proj_row.capital_asignado)
                if proj_row.capital_asignado is not None
                else None
            ),
        },
        "latest_sleep_report": latest_sleep_report,
        "active_rules_count": len(active_rules),
        "q_table_version": (
            int(latest_q.version) if latest_q is not None else None
        ),
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# System prompt — two blocks. Block 1 has cache_control; block 2 never does.
# ---------------------------------------------------------------------------


#: Static portion of the system prompt — role + tool catalogue + Aether
#: domain context. This must be invariant for prompt caching to kick in;
#: any drift between requests defeats the cache.
_STATIC_BLOCK_TEXT: str = (
    "# Rol\n"
    "Eres el asistente operativo de Aether Trading System. Hablas en "
    "español. Tu objetivo es ayudar al operador humano a entender el "
    "estado de un proyecto de trading concreto, sus métricas, sus "
    "reglas semánticas activas y sus reflexiones de la fase de sueño.\n"
    "\n"
    "# Reglas duras\n"
    "1. NUNCA propongas ni ejecutes operaciones (envío de órdenes, "
    "modificación de SL/TP, cierres). Esta superficie es de SOLO "
    "LECTURA. Si el usuario lo pide, recuérdale que el flujo de "
    "aprobación llega en una versión posterior.\n"
    "2. Preserva la regla del Charter: capital preservation > profit "
    "generation. Si una observación implica riesgo elevado, dilo "
    "explícitamente.\n"
    "3. Todas las herramientas que tienes a tu disposición operan "
    "exclusivamente sobre el par del contexto actual. NO intentes "
    "pasar `user_id`, `pair_id` ni `conversation_id` como parámetros "
    "— el backend los inyecta y los ignoraría si los enviases.\n"
    "4. Si una herramienta devuelve datos vacíos, dilo claramente al "
    "operador en lugar de inventar valores.\n"
    "5. Razona paso a paso antes de invocar herramientas: primero "
    "describe qué quieres saber y por qué, luego llama a la herramienta.\n"
    "\n"
    "# Catálogo de herramientas\n"
    "- `get_project_status` — estado de alto nivel del proyecto "
    "(símbolo, timeframe, status, equity si MCP está disponible).\n"
    "- `get_recent_trades` — operaciones cerradas o vivas del proyecto "
    "en una ventana temporal (horas hacia atrás).\n"
    "- `get_sleep_reports` — últimos informes de fase de sueño (resumen "
    "markdown + score).\n"
    "- `get_qtable_summary` — estados más frecuentes y distribución de "
    "acciones de la última versión de la Q-Table.\n"
    "- `get_semantic_rules` — reglas activas en memoria semántica, "
    "filtrables por tipo.\n"
    "\n"
    "# Estilo de respuesta\n"
    "- Sé conciso. Usa listas y tablas markdown si ayudan a leer.\n"
    "- Cuando termines de razonar, etiqueta la conclusión con "
    "`**DECISIÓN FINAL:**` y clasifica el riesgo asociado como Bajo / "
    "Medio / Alto si aplica.\n"
)


def build_system_prompt(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the two-block system-prompt list for the Anthropic call.

    Block 1 is the static text plus ``cache_control={"type":"ephemeral"}``.
    Block 2 is the JSON-serialised snapshot, NO cache_control.
    """
    import json

    static_block: dict[str, Any] = {
        "type": "text",
        "text": _STATIC_BLOCK_TEXT,
        "cache_control": {"type": "ephemeral"},
    }
    dynamic_text = (
        "# Snapshot del par (estado dinámico, no cacheado)\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    )
    dynamic_block: dict[str, Any] = {
        "type": "text",
        "text": dynamic_text,
    }
    return [static_block, dynamic_block]


__all__ = [
    "DEFAULT_MAX_TOOL_ROUNDTRIPS",
    "ChatDispatchContext",
    "build_pair_snapshot",
    "build_system_prompt",
]
