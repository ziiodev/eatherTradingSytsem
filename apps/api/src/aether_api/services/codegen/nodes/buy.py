"""Buy node — opens a long market order."""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    CALC_LOTS_NAME,
    CALC_LOTS_SRC,
    PIP_FACTOR_EXPR,
    TRAILING_NAME,
    TRAILING_SRC,
    comment_header,
    node_id,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Buy"


def generate(node: Node, connections: Connections) -> str:
    """Emit an MQL5 buy market order using the standard Trade library.

    When the graph contains a RiskManagement node AND this node sets
    ``sl_pips > 0``, the stop is derived from pips and the lot size is computed
    via the shared ``CalcLots`` helper. Otherwise the literal ``lots`` and raw
    ``stop_loss`` are emitted exactly as before (byte-identical).
    """
    lots = param(node, "lots", 0.1)
    sl = param(node, "stop_loss", 0)
    tp = param(node, "take_profit", 0)
    sl_pips = param(node, "sl_pips", 0)
    tp_pips = param(node, "tp_pips", 0)
    trail_pips = param(node, "trail_pips", 0)
    trail_start_pips = param(node, "trail_start_pips", 0)
    trail_step_pips = param(node, "trail_step_pips", 0)
    ctx = connections.context

    # Trailing-stop position management (decoupled from entry / SL / TP). When
    # enabled, register the file-scope helper once and contribute an OnTick-
    # prologue call with INLINE literal pip distances. Buy and Sell emit a
    # character-identical call (the helper branches on POSITION_TYPE), so the
    # prologue dedup coalesces them. trail_pips==0 emits nothing extra.
    if float(trail_pips) > 0:
        ctx.add_helper(TRAILING_NAME, TRAILING_SRC)
        ctx.add_prologue(
            f"   ManageTrailing({trail_pips} * ({PIP_FACTOR_EXPR}), "
            f"{trail_start_pips} * ({PIP_FACTOR_EXPR}), "
            f"{trail_step_pips} * ({PIP_FACTOR_EXPR}));"
        )

    # Take-profit-by-pips substitution (independent of SL / RiskManagement).
    # Gated on tp_pips alone — TP never feeds CalcLots. Default: byte-identical
    # passthrough of the raw take_profit value.
    tp_prelude = ""
    tp_arg = tp
    if float(tp_pips) > 0:
        tp_pip = f"tpPip_{node_id(node)}"
        tp_dist = f"tpDist_{node_id(node)}"
        tp_price = f"tpPrice_{node_id(node)}"
        tp_prelude = (
            f"   double {tp_pip} = {PIP_FACTOR_EXPR};\n"
            f"   double {tp_dist} = {tp_pips} * {tp_pip};\n"
            f"   double askTp_{node_id(node)} = "
            f"SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n"
            f"   double {tp_price} = askTp_{node_id(node)} + {tp_dist};\n"
        )
        tp_arg = tp_price

    if ctx.has_risk_node and float(sl_pips) > 0:
        ctx.add_helper(CALC_LOTS_NAME, CALC_LOTS_SRC)
        pip = f"pip_{node_id(node)}"
        sl_dist = f"slDist_{node_id(node)}"
        sl_price = f"slPrice_{node_id(node)}"
        return (
            f"{comment_header(node, NODE_TYPE)}\n"
            f"   double {pip} = {PIP_FACTOR_EXPR};\n"
            f"   double {sl_dist} = {sl_pips} * {pip};\n"
            f"   double ask_{node_id(node)} = "
            f"SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n"
            f"   double {sl_price} = ask_{node_id(node)} - {sl_dist};\n"
            f"{tp_prelude}"
            f"   trade.Buy(CalcLots({ctx.risk_percent}, {sl_dist}), "
            f'_Symbol, 0.0, {sl_price}, {tp_arg}, "buy");'
        )

    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"{tp_prelude}"
        f'   trade.Buy({lots}, _Symbol, 0.0, {sl}, {tp_arg}, "buy");'
    )
