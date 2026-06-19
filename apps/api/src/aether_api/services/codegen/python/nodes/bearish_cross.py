"""BearishCross node — guard firing when value1 crosses BELOW value2."""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._crossing import crossing_guard
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "BearishCross"


def generate(node: Node, connections: Connections) -> str:
    """Emit the bearish-cross early-return guard (or a no-op if unwired)."""
    return crossing_guard(node, NODE_TYPE, bullish=False, connections=connections)
