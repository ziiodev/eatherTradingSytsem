"""LogicalOr node — OR-combine upstream Condition exprs: ``(e1 or e2 or ...)``."""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._combinator import (
    guard_or_noop,
    incoming_condition_exprs,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "LogicalOr"


def generate(node: Node, connections: Connections) -> str:
    """Emit one early-return guard OR-combining the incoming Condition exprs."""
    exprs = incoming_condition_exprs(node, connections)
    if not exprs:
        return guard_or_noop(node, NODE_TYPE, None)
    return guard_or_noop(node, NODE_TYPE, "(" + " or ".join(exprs) + ")")
