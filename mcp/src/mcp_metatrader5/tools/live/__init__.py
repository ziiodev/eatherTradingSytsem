"""Live-trading tool surface for ``mcp-metatrader5``.

This subpackage was added by the ``mt5-integration`` change. It exposes 7
new MCP tools that wrap the official ``MetaTrader5`` Python package (or an
equivalent in-Wine bridge):

* ``mt5_get_account``
* ``mt5_get_positions``
* ``mt5_get_history``
* ``mt5_get_candles``
* ``mt5_place_order``
* ``mt5_modify_order``
* ``mt5_close_order``

Charter rules (enforced at the wrapper boundary, not the broker):

* The server **MUST NOT** generate MQL5 source, register or compile an
  Expert Advisor, or route live-trading orders through the Strategy
  Tester. Live orders flow through the official ``MetaTrader5`` Python
  binding only.
* Every order MUST carry a non-null stop loss (``sl``). The wrapper
  surfaces ``CHARTER_VIOLATION_MISSING_SL`` (an :class:`ErrorCode`) when a
  caller omits one, BEFORE any broker round-trip.
* ``mt5_modify_order`` MUST refuse to clear an existing stop loss to
  zero / null — flagged as ``CHARTER_VIOLATION_MISSING_SL`` as well.

All tool functions are pure with respect to the MCP runtime (no FastMCP
imports here) — the server entrypoint :mod:`mcp_metatrader5.server`
wires them via :func:`register_live_tools`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ...config import Settings
from .schemas import (
    CloseOrderInput,
    CloseOrderOutput,
    GetAccountInput,
    GetAccountOutput,
    GetCandlesInput,
    GetCandlesOutput,
    GetHistoryInput,
    GetHistoryOutput,
    GetPositionsInput,
    GetPositionsOutput,
    ModifyOrderInput,
    ModifyOrderOutput,
    PlaceOrderInput,
    PlaceOrderOutput,
)
from .tools import (
    close_order as _close_order,
)
from .tools import (
    get_account as _get_account,
)
from .tools import (
    get_candles as _get_candles,
)
from .tools import (
    get_history as _get_history,
)
from .tools import (
    get_positions as _get_positions,
)
from .tools import (
    modify_order as _modify_order,
)
from .tools import (
    place_order as _place_order,
)


def register_live_tools(app: FastMCP, settings: Settings) -> None:
    """Register the 7 live-trading tools on ``app``.

    The :class:`Settings` argument is the same instance the server already
    owns; this function only reads it (never mutates) so a test harness
    can pass a stub settings object with ``live_enabled=False`` to verify
    the LIVE_DISABLED gate.
    """

    @app.tool(
        name="mt5_get_account",
        description=(
            "Return the connected MT5 terminal's account snapshot "
            "(balance, equity, margin, free_margin, leverage, currency, login)."
        ),
    )
    def mt5_get_account(payload: GetAccountInput) -> GetAccountOutput:
        del payload  # no inputs; accepted for schema parity
        return _get_account(settings=settings)

    @app.tool(
        name="mt5_get_positions",
        description=(
            "Return open positions on the connected MT5 terminal. "
            "Optional symbol filter."
        ),
    )
    def mt5_get_positions(payload: GetPositionsInput) -> GetPositionsOutput:
        return _get_positions(payload, settings=settings)

    @app.tool(
        name="mt5_get_history",
        description=(
            "Return historical deals on the connected MT5 terminal "
            "between two ISO-8601 timestamps."
        ),
    )
    def mt5_get_history(payload: GetHistoryInput) -> GetHistoryOutput:
        return _get_history(payload, settings=settings)

    @app.tool(
        name="mt5_get_candles",
        description=(
            "Return OHLCV candles for ``symbol`` on ``timeframe``. "
            "``count`` is the number of most-recent bars to return."
        ),
    )
    def mt5_get_candles(payload: GetCandlesInput) -> GetCandlesOutput:
        return _get_candles(payload, settings=settings)

    @app.tool(
        name="mt5_place_order",
        description=(
            "Place a market or pending order. Stop loss is MANDATORY "
            "(charter rule). Returns the broker-assigned ticket."
        ),
    )
    def mt5_place_order(payload: PlaceOrderInput) -> PlaceOrderOutput:
        return _place_order(payload, settings=settings)

    @app.tool(
        name="mt5_modify_order",
        description=(
            "Modify SL/TP on an existing order or open position. SL cannot be "
            "removed (charter rule)."
        ),
    )
    def mt5_modify_order(payload: ModifyOrderInput) -> ModifyOrderOutput:
        return _modify_order(payload, settings=settings)

    @app.tool(
        name="mt5_close_order",
        description=(
            "Close an open position by ticket. Returns the close ticket "
            "and the deal price."
        ),
    )
    def mt5_close_order(payload: CloseOrderInput) -> CloseOrderOutput:
        return _close_order(payload, settings=settings)


__all__ = ["register_live_tools"]
