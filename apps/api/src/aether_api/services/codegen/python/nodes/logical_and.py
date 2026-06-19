"""LogicalAnd node — combine upstream Condition exprs with AND / count threshold.

Two modes (param ``requerir_todas``):
  - True  -> ALL must hold:  ``(e1 and e2 and ... and eN)``
  - False -> at least ``min_true`` hold (count mode):
             ``(((1 if e1 else 0)+(1 if e2 else 0)+...) >= min_true)``
"""

from __future__ import annotations

from aether_api.services.codegen.python.nodes._combinator import (
    guard_or_noop,
    incoming_condition_exprs,
)
from aether_api.services.codegen.python.pyhelpers import param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "LogicalAnd"


def generate(node: Node, connections: Connections) -> str:
    """Emit one early-return guard combining the incoming Condition exprs."""
    exprs = incoming_condition_exprs(node, connections)
    if not exprs:
        return guard_or_noop(node, NODE_TYPE, None)
    if param(node, "requerir_todas", False):
        combined = "(" + " and ".join(exprs) + ")"
    else:
        min_true = param(node, "min_true", 2)
        counted = "+".join(f"(1 if {e} else 0)" for e in exprs)
        combined = f"(({counted}) >= {min_true})"
    return guard_or_noop(node, NODE_TYPE, combined)
