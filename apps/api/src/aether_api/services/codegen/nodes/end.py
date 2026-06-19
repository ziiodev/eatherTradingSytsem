"""End node — marks the terminal point of the strategy logic."""

from __future__ import annotations

from aether_api.services.codegen.helpers import comment_header
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "End"


def generate(node: Node, connections: Connections) -> str:
    """Emit the closing of the per-tick strategy block."""
    return f"{comment_header(node, NODE_TYPE)}\n   // --- Strategy logic ends ---"
