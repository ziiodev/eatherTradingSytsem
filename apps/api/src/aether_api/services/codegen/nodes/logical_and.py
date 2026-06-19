"""LogicalAnd node — combine upstream Condition expressions with AND / count.

Two modes (param ``requerir_todas``):
  - True  -> ALL must hold:  ``(e1 && e2 && … && eN)``
  - False -> at least ``min_true`` hold (count mode):
             ``((((e1)?1:0)+((e2)?1:0)+…) >= min_true)``
"""

from __future__ import annotations

from aether_api.services.codegen.helpers import param
from aether_api.services.codegen.nodes._combinator import (
    guard_or_noop,
    incoming_condition_exprs,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "LogicalAnd"


def generate(node: Node, connections: Connections) -> str:
    """Emit one early-return guard combining the incoming Condition exprs."""
    exprs = incoming_condition_exprs(node, connections)
    if not exprs:
        return guard_or_noop(node, NODE_TYPE, None)

    requerir_todas = param(node, "requerir_todas", False)
    if requerir_todas:
        combined = "(" + " && ".join(exprs) + ")"
    else:
        min_true = param(node, "min_true", 2)
        # Each ``e`` is already a parenthesized comparison ``(left op right)``,
        # so ``(e?1:0)`` yields the spec form ``((left op right)?1:0)``.
        counted = "+".join(f"({e}?1:0)" for e in exprs)
        combined = f"(({counted}) >= {min_true})"
    return guard_or_noop(node, NODE_TYPE, combined)
