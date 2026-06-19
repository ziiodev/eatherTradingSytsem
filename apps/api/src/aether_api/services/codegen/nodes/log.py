"""Log node — prints a message to the MetaTrader Experts journal."""

from __future__ import annotations

from aether_api.services.codegen.helpers import comment_header, param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Log"


def generate(node: Node, connections: Connections) -> str:
    """Emit an MQL5 Print() call with the node's message."""
    message = str(param(node, "message", "log")).replace('"', '\\"')
    return f"{comment_header(node, NODE_TYPE)}\n   Print(\"{message}\");"
