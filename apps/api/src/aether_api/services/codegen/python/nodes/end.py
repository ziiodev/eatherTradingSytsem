"""End node — marks the terminal point of the per-bar strategy logic."""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import comment
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "End"


def generate(node: Node, connections: Connections) -> str:
    """Emit the closing landmark of the on_bar strategy block."""
    return f"{comment(node, NODE_TYPE)}\n    # --- Strategy logic ends ---"
