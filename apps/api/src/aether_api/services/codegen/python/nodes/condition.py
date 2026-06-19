"""Condition node — emits a None-safe early-return guard gating downstream nodes."""

from __future__ import annotations

from aether_api.services.codegen.python.operands import translate_operand
from aether_api.services.codegen.python.pyhelpers import IND, comment, param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Condition"


def condition_expr(node: Node, nodes_by_id: dict[str, Node]) -> str:
    """Return the bare boolean expression ``_cmp(left, "op", right)``.

    Shared by this node's guard and by the boolean combinators that inline a
    Condition. Each operand is translated to a Python read; the ``_cmp`` helper
    (in the skeleton) is None-safe, so a warmup ``None`` operand yields False
    instead of raising.
    """
    left = translate_operand(str(param(node, "left", "0")), nodes_by_id)
    operator = param(node, "operator", ">")
    right = translate_operand(str(param(node, "right", "0")), nodes_by_id)
    return f'_cmp({left}, "{operator}", {right})'


def generate(node: Node, connections: Connections) -> str:
    """Emit ``if not <expr>: return signals`` — aborts the bar when false."""
    expr = condition_expr(node, connections.context.nodes_by_id)
    return f"{comment(node, NODE_TYPE)}\n{IND}if not {expr}: return signals"
