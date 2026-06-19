"""Shared plumbing for crossing nodes (BullishCross / BearishCross).

A crossing consumes TWO indicator outputs through VALUE edges (handles
``value1``/``value2``) and emits ONE early-return guard occupying the single
flow-chain slot a Condition occupies. It compares the two buffers on the
confirmation bar ``s`` against the bar before (``s+1``) to detect a cross.

Both directions share this builder; only the comparison operators differ:
  - Bullish: V1 was below V2 (``[s+1]``) and is now above (``[s]``).
  - Bearish: V1 was above and is now below.
"""

from __future__ import annotations

from aether_api.services.codegen.buffer_ref import (
    ZSCORE_ARRAY_OUTPUTS,
    buffer_var,
)
from aether_api.services.codegen.helpers import PIP_FACTOR_EXPR, comment_header, param
from aether_api.services.codegen.types import Connections, Node

# ZScore array-output sourceHandle -> its buffer-var prefix (matches zscore.py).
_ZSCORE_ARRAY_PREFIX: dict[str, str] = {
    "zmean": "zmean_",
    "zstd": "zstd_",
    "zsma": "zsma_",
}


def _resolve_operand(
    node: Node, connections: Connections, handle: str
) -> str | None:
    """Return the bare buffer var feeding ``handle`` (value1/value2), or None.

    Output-aware. Resolves the incoming VALUE-edge source on ``handle`` to its
    node via ``ctx.nodes_by_id``, then to a bare array-var name:

    * SMA/RSI/MACD -> ``buffer_var`` (``sma_<id>`` etc.), source-handle agnostic.
    * ZScore -> ONLY its ARRAY outputs (sourceHandle ``zmean``/``zstd``/``zsma``)
      resolve to ``z<name>_<id>``. A ZScore scalar (``value``/``zabs``) or signal
      (``zgt``/``zlt``) source is REJECTED (returns None) — a cross needs a
      bar-indexable array, not a scalar/boolean.

    The crossing then hand-appends its own ``[s]``/``[s+1]`` indices (raw — no
    exclusive ``+1``), so SMA/RSI/MACD stay byte-identical.
    """
    nodes_by_id = connections.context.nodes_by_id
    nid = str(node.get("id", ""))
    for src_id, _tgt_handle, src_handle in connections.incoming_handled(nid):
        if _tgt_handle != handle:
            continue
        src = nodes_by_id.get(src_id)
        if src is None:
            continue
        if _domain_type(src).lower() == "zscore":
            if src_handle in ZSCORE_ARRAY_OUTPUTS:
                return f"{_ZSCORE_ARRAY_PREFIX[src_handle]}{src_id}"
            return None  # scalar/signal ZScore output is not crossable
        ref = buffer_var(src)
        if ref is not None:
            return ref
    return None


def _domain_type(node: Node) -> str:
    """Resolve a node's domain type from ``type`` or nested ``data.type``."""
    top = str(node.get("type") or "")
    if top and top.lower() != "custom":
        return top
    data = node.get("data") or {}
    return str(data.get("type", ""))


def crossing_guard(node: Node, label: str, *, bullish: bool, connections: Connections) -> str:
    """Build the crossing early-return guard (or a no-op comment if unwired).

    ``bullish`` flips the cross direction. Reads the four params
    (barras_confirmacion ``s``, distancia_minima_pips, filtrar_ruido,
    usar_metodo_desplazamiento — the last is read-but-ignored in v1). A missing
    value1/value2 operand yields a no-op comment and NO guard so an unwired
    crossing never halts the strategy.
    """
    header = comment_header(node, label)
    v1 = _resolve_operand(node, connections, "value1")
    v2 = _resolve_operand(node, connections, "value2")
    if v1 is None or v2 is None:
        return f"{header}\n   // [{label}] missing value input — no-op"

    s = int(param(node, "barras_confirmacion", 1))
    pips = param(node, "distancia_minima_pips", 0)
    filtrar_ruido = param(node, "filtrar_ruido", True)
    # usar_metodo_desplazamiento is intentionally read-but-ignored in v1.
    param(node, "usar_metodo_desplazamiento", False)

    before_op = ("<" if bullish else ">") if filtrar_ruido else ("<=" if bullish else ">=")
    now_op = ">" if bullish else "<"
    before = f"{v1}[{s + 1}] {before_op} {v2}[{s + 1}]"
    now = f"{v1}[{s}] {now_op} {v2}[{s}]"
    pip_term = ""
    if _positive(pips):
        pip_term = f" && MathAbs({v1}[{s}] - {v2}[{s}]) >= {pips}*{PIP_FACTOR_EXPR}"
    return f"{header}\n   if (!({before} && {now}{pip_term})) return;"


def _positive(value: object) -> bool:
    """True when a numeric param is strictly greater than zero."""
    try:
        return float(value) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
