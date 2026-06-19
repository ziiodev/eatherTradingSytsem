"""LogicalXor node — pass iff EXACTLY ONE upstream Condition is true.

Emits ``(((1 if e1 else 0) + ... + (1 if eN else 0)) == 1)``: counts the true
incoming exprs and requires the count to equal one (XOR over arbitrary fan-in).
"""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._combinator import (
    guard_or_noop,
    incoming_condition_exprs,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "LogicalXor"


def generate(node: Node, connections: Connections) -> str:
    """Emit one early-return guard requiring exactly one incoming Condition."""
    exprs = incoming_condition_exprs(node, connections)
    if not exprs:
        return guard_or_noop(node, NODE_TYPE, None)
    terms = [f"(1 if {e} else 0)" for e in exprs]
    combined = "((" + " + ".join(terms) + ") == 1)"
    return guard_or_noop(node, NODE_TYPE, combined)
