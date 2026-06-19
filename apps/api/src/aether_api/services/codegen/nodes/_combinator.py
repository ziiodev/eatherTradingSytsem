"""Shared plumbing for boolean combinator nodes (And / Or / Not).

A combinator fans in upstream Condition expressions and emits ONE early-return
guard occupying the single chain slot a Condition occupies today. It reads its
incoming Condition source ids via :class:`Connections` and resolves them to node
dicts through ``ctx.nodes_by_id``, then inlines each Condition's bare
``(left op right)`` via :func:`condition_expr`.
"""

from __future__ import annotations

from aether_api.services.codegen.buffer_ref import (
    ZSCORE_SIGNAL_OUTPUTS,
    resolve_output,
)
from aether_api.services.codegen.helpers import comment_header
from aether_api.services.codegen.nodes.condition import condition_expr
from aether_api.services.codegen.types import Connections, Node


def incoming_condition_exprs(node: Node, connections: Connections) -> list[str]:
    """Return the bare boolean expressions feeding this combinator.

    Two source families are accepted (topology guarantees the rest):

    * a Condition source -> inlined as ``(left op right)`` via ``condition_expr``.
    * a ZScore SIGNAL output (sourceHandle ``zgt``/``zlt``) -> resolved through
      :func:`resolve_output` to ``(z_<id> > 0)`` / ``(z_<id> < 0)``.

    Mixed Condition + ZScore-signal inputs work: each edge is resolved by its own
    source kind. Order follows the edge insertion order recorded by
    ``Connections`` (handle-aware), so it matches the legacy Condition-only order.
    """
    nodes_by_id = connections.context.nodes_by_id
    nid = node_id_of(node, connections)
    exprs: list[str] = []
    for src_id, _tgt_handle, src_handle in connections.incoming_handled(nid):
        src = nodes_by_id.get(src_id)
        if src is None:
            continue
        dtype = _domain_type(src).lower()
        if dtype == "condition":
            exprs.append(condition_expr(src, nodes_by_id))
        elif dtype == "zscore" and src_handle in ZSCORE_SIGNAL_OUTPUTS:
            resolved = resolve_output(src, src_handle, 0)
            if resolved is not None:
                exprs.append(resolved)
    return exprs


def node_id_of(node: Node, connections: Connections) -> str:  # noqa: ARG001
    """Return the node id as a string (kept tiny for testability)."""
    return str(node.get("id", ""))


def _domain_type(node: Node) -> str:
    """Resolve a node's domain type from ``type`` or nested ``data.type``."""
    top = str(node.get("type") or "")
    if top and top.lower() != "custom":
        return top
    data = node.get("data") or {}
    return str(data.get("type", ""))


def guard_or_noop(node: Node, label: str, expr: str | None) -> str:
    """Emit ``if (!<expr>) return;`` or, when no input, a no-op comment.

    ``expr`` is the fully parenthesized combined expression (e.g. ``(a && b)``),
    so the guard mirrors the Condition node's ``if (!<condition_expr>) return;``
    shape. ZERO connected inputs -> emit only a comment and NO guard, so an
    unwired combinator never halts the strategy.
    """
    header = comment_header(node, label)
    if expr is None:
        return f"{header}\n   // [{label}] no inputs connected — no-op"
    return f"{header}\n   if (!{expr}) return;"
