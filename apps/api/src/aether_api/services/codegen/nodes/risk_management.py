"""RiskManagement node — emits position-sizing / risk guard helpers."""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    CALC_LOTS_NAME,
    CALC_LOTS_SRC,
    comment_header,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "RiskManagement"


def generate(node: Node, connections: Connections) -> str:
    """Emit a simple max-open-positions and risk-per-trade guard.

    Also registers the shared file-scope ``CalcLots`` helper (idempotently) so
    risk-gated Buy/Sell nodes can size positions. Registration here means the
    helper is available regardless of node-walk order.
    """
    connections.context.add_helper(CALC_LOTS_NAME, CALC_LOTS_SRC)
    max_positions = param(node, "max_positions", 1)
    risk_percent = param(node, "risk_percent", 1.0)
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   // Risk: max {max_positions} position(s), {risk_percent}% per trade\n"
        f"   if (PositionsTotal() >= {max_positions})\n"
        f"      return;"
    )
