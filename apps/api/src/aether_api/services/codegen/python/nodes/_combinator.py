"""Shared plumbing for boolean combinator nodes (And / Or / Not / Xor).

Mirrors the MQL5 ``nodes/_combinator.py``: a combinator fans in upstream Condition
expressions (inlined as ``_cmp(...)``) plus ZScore SIGNAL outputs (zgt/zlt), then
emits ONE early-return guard occupying the chain slot a Condition would. Resolution
is handle-aware via :class:`Connections`, so the order matches the legacy walk.
"""

from __future__ import annotations

from aether_api.services.codegen.buffer_ref import ZSCORE_SIGNAL_OUTPUTS
from aether_api.services.codegen.graph import resolve_node_type
from aether_api.services.codegen.python.nodes.condition import condition_expr
from aether_api.services.codegen.python.pyhelpers import IND, comment
from aether_api.services.codegen.types import Connections, Node


def incoming_condition_exprs(node: Node, connections: Connections) -> list[str]:
    """Return the bare boolean expressions feeding this combinator.

    A Condition source is inlined via ``condition_expr``; a ZScore SIGNAL output
    (sourceHandle ``zgt``/``zlt``) becomes a None-safe sign test on ``z_<id>``.
    """
    nodes_by_id = connections.context.nodes_by_id
    nid = str(node.get("id", ""))
    exprs: list[str] = []
    for src_id, _tgt_handle, src_handle in connections.incoming_handled(nid):
        src = nodes_by_id.get(src_id)
        if src is None:
            continue
        dtype = resolve_node_type(src).lower()
        if dtype == "condition":
            exprs.append(condition_expr(src, nodes_by_id))
        elif dtype == "zscore" and src_handle in ZSCORE_SIGNAL_OUTPUTS:
            op = ">" if src_handle == "zgt" else "<"
            exprs.append(f'((_at(ind["z_{src_id}"], i, 0) or 0.0) {op} 0)')
    return exprs


def guard_or_noop(node: Node, label: str, expr: str | None) -> str:
    """Emit ``if not <expr>: return signals`` or a no-op comment when unwired."""
    header = comment(node, label)
    if expr is None:
        return f"{header}\n{IND}# [{label}] no inputs connected — no-op"
    return f"{header}\n{IND}if not {expr}: return signals"
