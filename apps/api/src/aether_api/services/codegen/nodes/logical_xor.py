"""LogicalXor node — pass iff EXACTLY ONE upstream Condition is true.

Emits ``((((e1)?1:0) + … + ((eN)?1:0)) == 1)`` as the combined expression: it
counts the true incoming Condition exprs and requires the count to equal one
(exclusive-or over an arbitrary fan-in). No params.
"""

from __future__ import annotations

from aether_api.services.codegen.nodes._combinator import (
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
    # Each ``e`` is already a parenthesized comparison ``(left op right)``, so
    # ``(e?1:0)`` yields ``((left op right)?1:0)`` — mirroring LogicalAnd's
    # count mode. XOR then requires the running sum to equal exactly one.
    terms = [f"({e}?1:0)" for e in exprs]
    combined = "((" + " + ".join(terms) + ") == 1)"
    return guard_or_noop(node, NODE_TYPE, combined)
