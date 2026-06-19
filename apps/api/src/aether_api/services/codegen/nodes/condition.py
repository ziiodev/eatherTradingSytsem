"""Condition node — emits a brace-free guard gating its downstream nodes."""

from __future__ import annotations

from aether_api.services.codegen.helpers import comment_header, param
from aether_api.services.codegen.nodes._operand_resolver import translate_operand
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Condition"


def condition_expr(node: Node, nodes_by_id: dict[str, Node]) -> str:
    """Return the bare comparison expression ``(left op right)`` for a Condition.

    Shared by this module's own guard and by the boolean combinators that inline
    a Condition's expression. Reads the SAME flat params with the SAME defaults.

    Each operand is routed through :func:`translate_operand`, which rewrites ONLY
    a ``<prefix>_<id>:<outputId>[shift]`` ref (a multi-output indicator's named
    output) into its real MQL5 read; plain refs and literals pass through string-
    identical, so the produced substring stays byte-identical to today's output.
    """
    left = translate_operand(str(param(node, "left", "0")), nodes_by_id)
    operator = param(node, "operator", ">")
    right = translate_operand(str(param(node, "right", "0")), nodes_by_id)
    return f"({left} {operator} {right})"


def generate(node: Node, connections: Connections) -> str:
    """Emit a brace-free MQL5 guard from a left/operator/right expression.

    The guard is a complete statement — ``if (!(<cond>)) return;`` — that aborts
    the current tick when the condition is false, gating every downstream node
    emitted after it by the dispatcher. Emitting a complete statement (rather
    than an open ``if (...) {`` brace) keeps the flat, per-node string
    concatenation in ``_render_body`` brace-balanced. The expression is
    intentionally simple and string-based to keep the node self-contained.
    """
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   if (!{condition_expr(node, connections.context.nodes_by_id)}) return;"
    )
