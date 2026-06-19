"""Shared plumbing for crossing nodes (BullishCross / BearishCross).

Mirrors the MQL5 ``nodes/_crossing.py``: a crossing consumes two indicator series
through VALUE edges (value1/value2) and emits ONE early-return guard. It compares
the two series on the confirmation bar ``s`` against the bar before (``s+1``) to
detect a cross. In the list world the reads are ``_at(series, i, s)`` /
``_at(series, i, s+1)``; a None (warmup) read makes the guard fail safely.
"""

from __future__ import annotations

from aether_api.services.codegen.buffer_ref import ZSCORE_ARRAY_OUTPUTS, buffer_var
from aether_api.services.codegen.graph import resolve_node_type
from aether_api.services.codegen.python.pyhelpers import IND, comment, param
from aether_api.services.codegen.types import Connections, Node

# ZScore array-output sourceHandle -> its series var prefix.
_ZSCORE_ARRAY_PREFIX = {"zmean": "zmean_", "zstd": "zstd_", "zsma": "zsma_"}


def _resolve_var(node: Node, connections: Connections, handle: str) -> str | None:
    """Return the bare series var feeding ``handle`` (value1/value2), or None.

    SMA/RSI/MACD -> ``buffer_var`` (``sma_<id>`` etc). ZScore -> ONLY its array
    outputs (zmean/zstd/zsma) resolve; a scalar/signal source is rejected.
    """
    nodes_by_id = connections.context.nodes_by_id
    nid = str(node.get("id", ""))
    for src_id, tgt_handle, src_handle in connections.incoming_handled(nid):
        if tgt_handle != handle:
            continue
        src = nodes_by_id.get(src_id)
        if src is None:
            continue
        if resolve_node_type(src).lower() == "zscore":
            if src_handle in ZSCORE_ARRAY_OUTPUTS:
                return f"{_ZSCORE_ARRAY_PREFIX[src_handle]}{src_id}"
            return None
        ref = buffer_var(src)
        if ref is not None:
            return ref
    return None


def _read(var: str, shift: int) -> str:
    """Return ``_at(ind["<var>"], i, <shift>)``."""
    return f'_at(ind["{var}"], i, {shift})'


def crossing_guard(
    node: Node, label: str, *, bullish: bool, connections: Connections
) -> str:
    """Build the crossing early-return guard (or a no-op comment if unwired)."""
    header = comment(node, label)
    v1 = _resolve_var(node, connections, "value1")
    v2 = _resolve_var(node, connections, "value2")
    if v1 is None or v2 is None:
        return f"{header}\n{IND}# [{label}] missing value input — no-op"

    s = int(param(node, "barras_confirmacion", 1))
    filtrar_ruido = param(node, "filtrar_ruido", True)
    param(node, "usar_metodo_desplazamiento", False)  # read-but-ignored in v1

    before_op = ("<" if bullish else ">") if filtrar_ruido else ("<=" if bullish else ">=")
    now_op = ">" if bullish else "<"
    before = f'_cmp({_read(v1, s + 1)}, "{before_op}", {_read(v2, s + 1)})'
    now = f'_cmp({_read(v1, s)}, "{now_op}", {_read(v2, s)})'
    return f"{header}\n{IND}if not ({before} and {now}): return signals"
