"""LogicalNot node — negate a single upstream Condition: ``(not e1)``."""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._combinator import (
    guard_or_noop,
    incoming_condition_exprs,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "LogicalNot"


def generate(node: Node, connections: Connections) -> str:
    """Emit one early-return guard negating the single incoming Condition."""
    exprs = incoming_condition_exprs(node, connections)
    if not exprs:
        return guard_or_noop(node, NODE_TYPE, None)
    # Defensive: topology blocks >1 input; use the first if more slip through.
    return guard_or_noop(node, NODE_TYPE, f"(not {exprs[0]})")
