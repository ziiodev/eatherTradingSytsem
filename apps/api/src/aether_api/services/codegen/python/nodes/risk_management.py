"""RiskManagement node — documents the graph-level risk policy.

The generated script is broker-agnostic and stateless per bar, so it cannot query
open positions or account balance. The risk policy (max positions, risk percent)
is therefore recorded as metadata: this node emits a comment, and risk-gated
Buy/Sell nodes attach ``risk_percent`` / ``sl_pips`` to their signals so a
downstream executor can size the order. This mirrors the MQL5 engine's split
(RiskManagement registers the sizing helper; the trade node uses it).
"""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "RiskManagement"


def generate(node: Node, connections: Connections) -> str:
    """Emit a documentation comment for the risk policy (no broker state)."""
    max_positions = param(node, "max_positions", 1)
    risk_percent = param(node, "risk_percent", 1.0)
    return (
        f"{comment(node, NODE_TYPE)}\n"
        f"{IND}# Risk: max {max_positions} position(s), {risk_percent}% per trade"
    )
