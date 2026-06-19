"""Log node — appends a log message to the bar's emitted signals."""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Log"


def generate(node: Node, connections: Connections) -> str:
    """Emit a log signal carrying the node's message (printed by the runner)."""
    message = str(param(node, "message", "log")).replace('"', '\\"')
    return (
        f"{comment(node, NODE_TYPE)}\n"
        f'{IND}signals.append({{"action": "log", "message": "{message}"}})'
    )
