"""Shared plumbing for the Buy / Sell trade nodes.

Both emit a signal dict appended to the bar's ``signals`` list. The fields mirror
the MQL5 trade call inputs (lots, sl, tp) plus pip-based variants and trailing
metadata. Because the generated script is broker-agnostic, prices are not derived
from the live bid/ask here — instead the pip distances / risk policy travel WITH
the signal so a downstream executor (or backtester) applies them. The two
directions differ only by the ``side`` literal.
"""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param
from aether_api.services.codegen.types import Connections, Node


def _num(value: object) -> float:
    """Best-effort numeric coercion (params arrive as numbers or strings)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def trade_signal(node: Node, side: str, connections: Connections) -> str:
    """Build the ``signals.append({...})`` line for a Buy/Sell node.

    Fields are added only when meaningful (non-zero), keeping the emitted dict
    compact. When a RiskManagement node exists AND ``sl_pips > 0``, the
    ``risk_percent`` from the graph is attached so the executor can size lots.
    """
    label = side.capitalize()
    lots = param(node, "lots", 0.1)
    sl = param(node, "stop_loss", 0)
    tp = param(node, "take_profit", 0)
    sl_pips = param(node, "sl_pips", 0)
    tp_pips = param(node, "tp_pips", 0)
    trail_pips = param(node, "trail_pips", 0)
    ctx = connections.context

    fields = [f'"action": "{side}"', f'"lots": {lots}']
    if _num(sl) != 0:
        fields.append(f'"sl": {sl}')
    if _num(tp) != 0:
        fields.append(f'"tp": {tp}')
    if _num(sl_pips) > 0:
        fields.append(f'"sl_pips": {sl_pips}')
        if ctx.has_risk_node:
            fields.append(f'"risk_percent": {ctx.risk_percent}')
    if _num(tp_pips) > 0:
        fields.append(f'"tp_pips": {tp_pips}')
    if _num(trail_pips) > 0:
        fields.append(f'"trail_pips": {trail_pips}')
        fields.append(f'"trail_start_pips": {param(node, "trail_start_pips", 0)}')
        fields.append(f'"trail_step_pips": {param(node, "trail_step_pips", 0)}')

    body = ", ".join(fields)
    return f"{comment(node, label)}\n{IND}signals.append({{{body}}})"
