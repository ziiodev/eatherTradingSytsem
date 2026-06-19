"""BullishCross node — guard firing when V1 crosses ABOVE V2.

Detects a bullish cross of two indicator buffers (value1 over value2): V1 was at
or below V2 on the prior confirmation bar and is strictly above on the current
one. Emits one early-return guard via the shared crossing builder.
"""

from __future__ import annotations

from aether_api.services.codegen.nodes._crossing import crossing_guard
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "BullishCross"


def generate(node: Node, connections: Connections) -> str:
    """Emit the bullish-cross early-return guard (or a no-op if unwired)."""
    return crossing_guard(node, NODE_TYPE, bullish=True, connections=connections)
