"""Buy node — appends a long-entry signal for the current bar."""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._trade import trade_signal
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Buy"


def generate(node: Node, connections: Connections) -> str:
    """Emit a buy signal dict (lots / sl / tp / pip + risk metadata)."""
    return trade_signal(node, "buy", connections)
