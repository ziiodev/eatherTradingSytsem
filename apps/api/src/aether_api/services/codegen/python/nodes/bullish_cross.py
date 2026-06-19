"""BullishCross node — guard firing when value1 crosses ABOVE value2."""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._crossing import crossing_guard
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "BullishCross"


def generate(node: Node, connections: Connections) -> str:
    """Emit the bullish-cross early-return guard (or a no-op if unwired)."""
    return crossing_guard(node, NODE_TYPE, bullish=True, connections=connections)
