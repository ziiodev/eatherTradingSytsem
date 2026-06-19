"""Small helpers shared by per-node generators."""

from __future__ import annotations

from typing import Any

from aether_api.services.codegen.types import Node


def node_data(node: Node) -> dict[str, Any]:
    """Return the node's `data` dict (React Flow stores params there)."""
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def node_id(node: Node) -> str:
    """Return the node id as a string."""
    return str(node.get("id", ""))


def param(node: Node, key: str, default: Any) -> Any:
    """Fetch a parameter from the node's data with a fallback default."""
    return node_data(node).get(key, default)


def comment_header(node: Node, label: str) -> str:
    """Return a one-line MQL5 comment identifying the node."""
    return f"   // [{label}] node id={node_id(node)}"


# Registry key + source for the shared risk-based position-sizing helper. Both
# RiskManagement and risk-gated Buy/Sell nodes register this via
# ``ctx.add_helper(CALC_LOTS_NAME, CALC_LOTS_SRC)`` so it is emitted file-scope
# exactly once regardless of registration order.
CALC_LOTS_NAME = "CalcLots"
CALC_LOTS_SRC = (
    "// Risk-based position sizing: lots for `riskPct`% of balance over `slDistance`.\n"
    "double CalcLots(double riskPct, double slDistance)\n"
    "{\n"
    "   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);\n"
    "   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);\n"
    "   double volMin    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);\n"
    "   double volMax    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);\n"
    "   double volStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);\n"
    "   if (slDistance <= 0.0 || tickSize <= 0.0)\n"
    "      return(volMin);\n"
    "   double lossPerLot = (slDistance / tickSize) * tickValue;\n"
    "   if (lossPerLot <= 0.0)\n"
    "      return(volMin);\n"
    "   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * riskPct / 100.0;\n"
    "   double lots = riskMoney / lossPerLot;\n"
    "   if (volStep > 0.0)\n"
    "      lots = MathFloor(lots / volStep) * volStep;\n"
    "   if (lots < volMin) lots = volMin;\n"
    "   if (lots > volMax) lots = volMax;\n"
    "   return(lots);\n"
    "}"
)

# Pip factor expression: 10x point on 3/5-digit (fractional-pip) symbols, else 1x.
PIP_FACTOR_EXPR = "_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1)"


def applied_price_series(applied_price: str, shift_expr: str) -> str:
    """Return an MQL5 bar-read expression for an ``applied_price`` at ``shift_expr``.

    Maps an MQL5 ``PRICE_*`` constant to the equivalent direct bar read on the
    current ``_Symbol`` / ``_Period`` at the (already-rendered) ``shift_expr``
    index. Used by indicators (e.g. ZScore) that must compare a single bar's
    price against an indicator buffer on the SAME bar, where a CopyBuffer of the
    raw price is overkill. ``shift_expr`` is interpolated verbatim, so callers
    may pass a literal (``"0"``), a variable, or a small expression (``"b + 1"``).

    Unknown / unmapped price constants fall back to ``PRICE_CLOSE`` so the output
    is always a valid double expression.
    """
    s = shift_expr
    table: dict[str, str] = {
        "PRICE_CLOSE": f"iClose(_Symbol, _Period, {s})",
        "PRICE_OPEN": f"iOpen(_Symbol, _Period, {s})",
        "PRICE_HIGH": f"iHigh(_Symbol, _Period, {s})",
        "PRICE_LOW": f"iLow(_Symbol, _Period, {s})",
        "PRICE_MEDIAN": (
            f"((iHigh(_Symbol, _Period, {s}) + iLow(_Symbol, _Period, {s})) / 2.0)"
        ),
        "PRICE_TYPICAL": (
            f"((iHigh(_Symbol, _Period, {s}) + iLow(_Symbol, _Period, {s})"
            f" + iClose(_Symbol, _Period, {s})) / 3.0)"
        ),
        "PRICE_WEIGHTED": (
            f"((iHigh(_Symbol, _Period, {s}) + iLow(_Symbol, _Period, {s})"
            f" + 2.0 * iClose(_Symbol, _Period, {s})) / 4.0)"
        ),
    }
    return table.get(applied_price, table["PRICE_CLOSE"])


def copy_buffer_lines(handle: str, buffer: str, var: str, depth: int) -> str:
    """Return the CopyBuffer read line(s) for an indicator buffer.

    ``depth == 1`` emits the BYTE-IDENTICAL legacy single-bar read
    ``CopyBuffer(<handle>, <buffer>, 0, 1, <var>);`` with no extra setup, so
    crossing-free graphs are unchanged. ``depth > 1`` (a crossing needs previous
    bars) first marks the destination array as series-indexed
    (``ArraySetAsSeries(<var>, true);``) so ``<var>[0]`` is the newest bar and
    ``<var>[s]``/``<var>[s+1]`` index back in time, then copies ``depth`` bars.
    """
    if depth <= 1:
        return f"   CopyBuffer({handle}, {buffer}, 0, 1, {var});"
    return (
        f"   ArraySetAsSeries({var}, true);\n"
        f"   CopyBuffer({handle}, {buffer}, 0, {depth}, {var});"
    )

# Registry key + source for the shared trailing-stop position manager. Trailing-
# enabled Buy/Sell nodes register this via
# ``ctx.add_helper(TRAILING_NAME, TRAILING_SRC)`` so it is emitted file-scope
# exactly once, and contribute an OnTick-prologue ``ManageTrailing(...)`` call
# that runs every tick before any entry guard. The helper branches internally on
# ``POSITION_TYPE`` so a Buy and a Sell share one character-identical call.
#
# `trailDist`  — distance (price units) kept between price and the trailing stop.
# `startDist`  — min profit (price units) required before trailing activates.
# `stepDist`   — min improvement (price units) before the stop is moved again.
# The existing take-profit is preserved (currentTP) on every PositionModify.
TRAILING_NAME = "ManageTrailing"
TRAILING_SRC = (
    "// Trailing stop: tighten the open position's SL by `trailDist`, gated on\n"
    "// `startDist` profit and `stepDist` minimum step. Preserves the open TP.\n"
    "void ManageTrailing(double trailDist, double startDist, double stepDist)\n"
    "{\n"
    "   if (trailDist <= 0.0)\n"
    "      return;\n"
    "   if (!PositionSelect(_Symbol))\n"
    "      return;\n"
    "   long   posType   = PositionGetInteger(POSITION_TYPE);\n"
    "   double currentSL = PositionGetDouble(POSITION_SL);\n"
    "   double currentTP = PositionGetDouble(POSITION_TP);\n"
    "   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);\n"
    "   double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);\n"
    "   double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n"
    "   if (posType == POSITION_TYPE_BUY)\n"
    "   {\n"
    "      if (bid - openPrice < startDist)\n"
    "         return;\n"
    "      double newSL = NormalizeDouble(bid - trailDist, _Digits);\n"
    "      if (currentSL == 0.0 || newSL - currentSL >= stepDist)\n"
    "         trade.PositionModify(_Symbol, newSL, currentTP);\n"
    "   }\n"
    "   else if (posType == POSITION_TYPE_SELL)\n"
    "   {\n"
    "      if (openPrice - ask < startDist)\n"
    "         return;\n"
    "      double newSL = NormalizeDouble(ask + trailDist, _Digits);\n"
    "      if (currentSL == 0.0 || currentSL - newSL >= stepDist)\n"
    "         trade.PositionModify(_Symbol, newSL, currentTP);\n"
    "   }\n"
    "}"
)
