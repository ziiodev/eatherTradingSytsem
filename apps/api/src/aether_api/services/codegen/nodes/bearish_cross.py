"""BearishCross node — guard firing when V1 crosses BELOW V2.

Detects a bearish cross of two indicator buffers (value1 under value2): V1 was at
or above V2 on the prior confirmation bar and is strictly below on the current
one. Emits one early-return guard via the shared crossing builder.
"""

from __future__ import annotations

from aether_api.services.codegen.nodes._crossing import crossing_guard
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "BearishCross"


def generate(node: Node, connections: Connections) -> str:
    """Emit the bearish-cross early-return guard (or a no-op if unwired)."""
    return crossing_guard(node, NODE_TYPE, bullish=False, connections=connections)
