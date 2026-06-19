"""LogicalNot node — negate a single upstream Condition expression.

Emits ``!(e1)`` as the combined expression. Exactly one input is expected;
topology blocks more than one. With ZERO inputs the node is a no-op (no guard).
"""

from __future__ import annotations

from aether_api.services.codegen.nodes._combinator import (
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
    combined = f"(!{exprs[0]})"
    return guard_or_noop(node, NODE_TYPE, combined)
