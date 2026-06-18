"""Read-only LLM tool surface for the project-chat dispatcher.

Five tool callables expose tenant-scoped reads of:

* the project header (``get_project_status``),
* recent orders (``get_recent_trades``),
* sleep-phase reports (``get_sleep_reports``),
* the most recent Q-Table summary (``get_qtable_summary``),
* active semantic-memory rules (``get_semantic_rules``).

Hard rules — enforced both by code paths AND by import-time assertion:

* No tool schema MAY declare ``user_id`` / ``project_id`` /
  ``conversation_id`` as input parameters. The LLM never gets to forge
  identity — the backend injects it from :class:`ChatDispatchContext`.
* The dispatcher sanitises the model's tool input by dropping those
  three keys before invoking the callable; even a model trained to
  hallucinate them cannot smuggle them into a repository call.
* Every tool opens a fresh DB session from ``ctx.db_session_factory``,
  performs its read, and closes the session. A tool failure produces
  ``{"is_error": True, "content": "Tool failed: <type>"}`` — never a
  stack trace; the LLM is hostile by default.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aether_api.repositories.order_repository import OrderRepository
from aether_api.repositories.pair_repository import PairRepository
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.semantic_memory_repository import (
    SemanticMemoryRepository,
)
from aether_api.repositories.sleep_report_repository import SleepReportRepository
from aether_api.services.chat.context import ChatDispatchContext

logger = logging.getLogger(__name__)

#: Keys the LLM is NEVER allowed to forge — the dispatcher drops them.
#: ``project_id`` retained alongside ``pair_id`` so a forged legacy key
#: is still stripped defensively.
_TENANCY_KEYS: frozenset[str] = frozenset(
    {"user_id", "pair_id", "project_id", "conversation_id"}
)


def _isoformat_or_none(value: Any) -> str | None:
    """Return ``value.isoformat()`` when ``value`` is a datetime; else ``None``.

    Defensive helper for tool payloads — ORM rows may carry ``None`` on
    optional timestamp columns, and we want a JSON-friendly string or
    ``null``, not a runtime AttributeError.
    """
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Catalogue entry: name → (callable, JSON schema, description)."""

    name: str
    description: str
    schema: dict[str, Any]
    callable: Callable[..., Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Tool implementations — each opens a short-lived DB session.
# ---------------------------------------------------------------------------


async def tool_get_project_status(
    ctx: ChatDispatchContext, **_kwargs: Any
) -> dict[str, Any]:
    """Return high-level project header + live equity if MCP is reachable."""
    async with ctx.db_session_factory() as session:
        repo = PairRepository(session)
        project = await repo.get_for_user(ctx.user_id, ctx.pair_id)
        if project is None:
            return {"project": None}
        return {
            "project": {
                "id": str(project.id),
                "name": project.name,
                "symbol": project.symbol,
                "timeframe": project.timeframe,
                "status": project.status,
                "mcp_url": project.mcp_url,
                "capital_asignado": (
                    float(project.capital_asignado)
                    if project.capital_asignado is not None
                    else None
                ),
            },
            # The chat dispatcher is intentionally decoupled from the
            # MCP live-bus in v1. ``equity`` is reported as ``None`` —
            # the LLM is told to interpret missing live data as
            # "unknown", never as "zero".
            "equity": None,
        }


async def tool_get_recent_trades(
    ctx: ChatDispatchContext,
    *,
    since_hours: int = 24,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return up to ``limit`` orders for the project, opened within
    the last ``since_hours`` hours.
    """
    # Coerce + clamp defensively — the LLM may pass strings or oversize ints.
    try:
        since_hours_int = int(since_hours)
    except (TypeError, ValueError):
        since_hours_int = 24
    since_hours_int = max(1, min(since_hours_int, 24 * 14))

    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 20
    limit_int = max(1, min(limit_int, 100))

    from_date = datetime.now(tz=UTC) - timedelta(hours=since_hours_int)

    async with ctx.db_session_factory() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(
            user_id=ctx.user_id,
            project_id=ctx.pair_id,
            from_date=from_date,
            limit=limit_int,
        )
    return {
        "since_hours": since_hours_int,
        "limit": limit_int,
        "total": total,
        "trades": [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side,
                "status": o.status,
                "volume": (
                    float(o.volume) if o.volume is not None else None
                ),
                "sl": float(o.sl) if o.sl is not None else None,
                "tp": float(o.tp) if o.tp is not None else None,
                "profit_net": (
                    float(o.profit_net)
                    if o.profit_net is not None
                    else None
                ),
                "open_time": (
                    o.open_time.isoformat() if o.open_time else None
                ),
                "close_time": _isoformat_or_none(
                    getattr(o, "close_time", None)
                ),
                "mt5_ticket": getattr(o, "mt5_ticket", None),
                "comment": o.comment,
            }
            for o in rows
        ],
    }


async def tool_get_sleep_reports(
    ctx: ChatDispatchContext,
    *,
    limit: int = 5,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return the last ``limit`` sleep-phase reports for the project."""
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 5
    limit_int = max(1, min(limit_int, 20))

    from sqlalchemy import select

    from aether_api.models.sleep_run import SleepRun

    async with ctx.db_session_factory() as session:
        repo = SleepReportRepository(session)
        # SleepReportRepository does not expose list_for_pair; query
        # the most recent N runs for the pair (tenancy enforced via
        # the JOIN to pairs.user_id below) and resolve their reports.
        from aether_api.models.pair import Pair

        run_stmt = (
            select(SleepRun.id, SleepRun.phase_type, SleepRun.ended_at,
                   SleepRun.started_at, SleepRun.status)
            .join(Pair, Pair.id == SleepRun.pair_id)
            .where(Pair.user_id == ctx.user_id)
            .where(SleepRun.pair_id == ctx.pair_id)
            .order_by(
                SleepRun.ended_at.desc().nulls_last(),
                SleepRun.started_at.desc().nulls_last(),
            )
            .limit(limit_int)
        )
        run_rows = list((await session.execute(run_stmt)).all())
        reports: list[dict[str, Any]] = []
        for run_id, phase_type, ended_at, started_at, status in run_rows:
            report = await repo.get_by_run_id(
                user_id=ctx.user_id, sleep_run_id=run_id
            )
            payload = (report.payload or {}) if report is not None else {}
            reports.append(
                {
                    "sleep_run_id": str(run_id),
                    "phase_type": phase_type,
                    "status": status,
                    "summary": (
                        report.summary_md if report is not None else None
                    ),
                    "overall_score": payload.get("overall_score"),
                    "started_at": (
                        started_at.isoformat() if started_at else None
                    ),
                    "ended_at": ended_at.isoformat() if ended_at else None,
                }
            )
    return {"limit": limit_int, "reports": reports}


async def tool_get_qtable_summary(
    ctx: ChatDispatchContext, **_kwargs: Any
) -> dict[str, Any]:
    """Return a compact summary of the latest Q-Table version.

    Includes the version number, episode count, alpha + gamma, the top
    states by frequency (from ``episodic_memory.top_k_states``) and a
    rough action distribution.
    """
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    async with ctx.db_session_factory() as session:
        q_repo = QTableRepository(session)
        latest = await q_repo.get_latest(
            user_id=ctx.user_id, project_id=ctx.pair_id
        )
        if latest is None:
            return {"q_table": None}

        ep_repo = EpisodicMemoryRepository(session)
        top_states = await ep_repo.top_k_states(
            user_id=ctx.user_id, project_id=ctx.pair_id, k=10
        )

        # Action distribution from the Q-Table data — count distinct
        # actions across all state entries. ``table_data`` is JSONB; we
        # treat it defensively because Sleep Phase versions evolve.
        action_counts: dict[str, int] = {}
        table = latest.table_data if isinstance(latest.table_data, dict) else {}
        for value in table.values():
            if not isinstance(value, dict):
                continue
            for action_key in value:
                if action_key.startswith("__"):
                    continue
                action_counts[action_key] = action_counts.get(action_key, 0) + 1

    return {
        "q_table": {
            "version": int(latest.version),
            "episode_count": int(getattr(latest, "episode_count", 0) or 0),
            "alpha_normal": (
                float(latest.alpha_normal)
                if getattr(latest, "alpha_normal", None) is not None
                else None
            ),
            "gamma": (
                float(latest.gamma)
                if getattr(latest, "gamma", None) is not None
                else None
            ),
            "top_states": [
                {"state_key": s, "frequency": n} for s, n in top_states
            ],
            "action_distribution": action_counts,
        }
    }


async def tool_get_semantic_rules(
    ctx: ChatDispatchContext,
    *,
    active: bool = True,
    rule_type: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return semantic-memory rules for the project.

    ``active`` is honoured (the underlying repository only exposes the
    active set in v1; passing ``False`` returns an empty list with a
    note so the LLM doesn't loop forever).
    """
    if not active:
        return {
            "rules": [],
            "note": (
                "v1 only exposes the active rule set; inactive / superseded "
                "rules are intentionally not surfaced to the chat."
            ),
        }

    async with ctx.db_session_factory() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(
            user_id=ctx.user_id,
            project_id=ctx.pair_id,
            rule_type=rule_type,
        )
    return {
        "rule_type": rule_type,
        "rules": [
            {
                "id": str(r.id),
                "rule_type": r.rule_type,
                "title": (r.payload or {}).get("title"),
                "body": r.body,
                "confidence": (r.payload or {}).get("confidence"),
                "source": (r.payload or {}).get("source"),
                "active": bool(r.active),
                "created_at": (
                    r.created_at.isoformat()
                    if getattr(r, "created_at", None)
                    else None
                ),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Catalogue — name → ToolSpec. Schemas pass the import-time tenancy gate.
# ---------------------------------------------------------------------------


TOOL_CATALOGUE: dict[str, ToolSpec] = {
    "get_project_status": ToolSpec(
        name="get_project_status",
        description=(
            "Devuelve el estado de alto nivel del proyecto: símbolo, "
            "timeframe, status, capital asignado y, si MCP está "
            "disponible, equity en vivo. Sin parámetros."
        ),
        schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        callable=tool_get_project_status,
    ),
    "get_recent_trades": ToolSpec(
        name="get_recent_trades",
        description=(
            "Devuelve operaciones recientes del proyecto en una ventana "
            "temporal. ``since_hours`` (1-336, default 24) controla la "
            "ventana; ``limit`` (1-100, default 20) acota el número de "
            "filas."
        ),
        schema={
            "type": "object",
            "properties": {
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 336,
                    "default": 24,
                    "description": "Horas hacia atrás desde ahora.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "Máximo de operaciones a devolver.",
                },
            },
            "additionalProperties": False,
        },
        callable=tool_get_recent_trades,
    ),
    "get_sleep_reports": ToolSpec(
        name="get_sleep_reports",
        description=(
            "Devuelve los últimos informes de fase de sueño del proyecto "
            "(resumen markdown + score global)."
        ),
        schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "additionalProperties": False,
        },
        callable=tool_get_sleep_reports,
    ),
    "get_qtable_summary": ToolSpec(
        name="get_qtable_summary",
        description=(
            "Devuelve un resumen de la Q-Table más reciente: versión, "
            "número de episodios, alpha, gamma, estados más frecuentes "
            "y distribución de acciones."
        ),
        schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        callable=tool_get_qtable_summary,
    ),
    "get_semantic_rules": ToolSpec(
        name="get_semantic_rules",
        description=(
            "Devuelve las reglas activas de memoria semántica del "
            "proyecto. ``rule_type`` (opcional) filtra por tipo."
        ),
        schema={
            "type": "object",
            "properties": {
                "active": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Si es False, devuelve lista vacía (v1 sólo "
                        "expone reglas activas)."
                    ),
                },
                "rule_type": {
                    "type": ["string", "null"],
                    "description": "Filtro opcional por tipo de regla.",
                },
            },
            "additionalProperties": False,
        },
        callable=tool_get_semantic_rules,
    ),
}


# ---------------------------------------------------------------------------
# Import-time gate: tool schemas MUST NOT declare tenancy parameters.
# ---------------------------------------------------------------------------


def _assert_no_tenancy_in_schema(spec: ToolSpec) -> None:
    """Refuse a schema that exposes tenancy keys to the LLM.

    The whole point of the dispatcher is that identity is server-side;
    a schema that names ``user_id`` invites prompt injection. We assert
    at import time so the test suite cannot run with a leaky surface.
    """
    properties = spec.schema.get("properties", {}) if isinstance(
        spec.schema, dict
    ) else {}
    leaked = set(properties.keys()) & _TENANCY_KEYS
    if leaked:
        raise RuntimeError(
            f"Tool {spec.name!r} schema exposes tenancy keys to the LLM: "
            f"{sorted(leaked)!r}. These MUST be server-injected."
        )


for _spec in TOOL_CATALOGUE.values():
    _assert_no_tenancy_in_schema(_spec)


# ---------------------------------------------------------------------------
# Dispatcher — sanitises input, invokes callable, returns LLM-shaped result.
# ---------------------------------------------------------------------------


def _sanitise_input(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop tenancy keys the LLM may have forged."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k not in _TENANCY_KEYS}


async def dispatch_tool(
    ctx: ChatDispatchContext,
    *,
    tool_use_id: str,
    tool_name: str,
    input: dict[str, Any],  # noqa: A002 — matches Anthropic SDK key
) -> dict[str, Any]:
    """Resolve ``tool_name``, invoke the callable, return an LLM-shaped dict.

    Result shape mirrors the Anthropic tool_result block:

    * Success → ``{"tool_use_id": ..., "is_error": False, "content": <dict>}``
    * Failure → ``{"tool_use_id": ..., "is_error": True,  "content": "Tool failed: ..."}``

    Never raises — we always feed the LLM something it can react to.
    Stack traces are logged at DEBUG so they remain available to ops
    without leaking to the model.
    """
    spec = TOOL_CATALOGUE.get(tool_name)
    if spec is None:
        return {
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": (
                f"Tool failed: unknown tool {tool_name!r}. "
                f"Valid names: {sorted(TOOL_CATALOGUE)!r}."
            ),
        }

    sanitised = _sanitise_input(input)
    try:
        result = await spec.callable(ctx, **sanitised)
        return {
            "tool_use_id": tool_use_id,
            "is_error": False,
            "content": result,
        }
    except TypeError as exc:
        # Bad kwargs — most likely a schema mismatch.
        logger.debug("Tool %s rejected args %r", tool_name, sanitised, exc_info=True)
        return {
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": f"Tool failed: invalid arguments ({type(exc).__name__}).",
        }
    except Exception as exc:  # noqa: BLE001 — opaque to LLM, logged for ops
        logger.debug(
            "Tool %s raised %s",
            tool_name,
            type(exc).__name__,
            exc_info=True,
        )
        return {
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": f"Tool failed: {type(exc).__name__}.",
        }


__all__ = [
    "TOOL_CATALOGUE",
    "ToolSpec",
    "dispatch_tool",
    "tool_get_project_status",
    "tool_get_qtable_summary",
    "tool_get_recent_trades",
    "tool_get_semantic_rules",
    "tool_get_sleep_reports",
]

# Module-level loop variable cleanup — keeps the import-time gate visible
# without leaving a leaked ``_spec`` reference in the module namespace.
del _spec  # noqa: F821 — defined above by the for-loop.
