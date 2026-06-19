"""Language-agnostic copy-depth scan shared by both codegen engines.

The depth map answers ONE question per indicator: how many bars of history must be
available so every consumer (a crossing reading ``[s]``/``[s+1]``, an operand
reading ``<var>[k]``, a ZScore self/array read) can resolve its bar index. The
MQL5 engine turns this into a ``CopyBuffer(..., depth, ...)``; the Python engine
turns it into the warmup window for its inline indicator series. Same map, two
back-ends — so it lives here next to :mod:`graph`.
"""

from __future__ import annotations

from typing import Any

from aether_api.services.codegen.buffer_ref import (
    ZSCORE_ARRAY_OUTPUTS,
    buffer_var,
)
from aether_api.services.codegen.graph import (
    _CROSSING_TYPES,
    _operand_read_index,
    _operand_shift,
    resolve_node_type,
)
from aether_api.services.codegen.helpers import param
from aether_api.services.codegen.types import Edge, Node, edge_role


def _indicator_copy_depth(
    nodes: list[Node], edges: list[Edge]
) -> dict[str, int]:
    """Return ``{indicator_id: depth}`` — the max CopyBuffer depth each needs.

    ONE max-wins scan folding three consumer kinds (the last two are WIRED but
    DORMANT — no shipping consumer triggers them, so every existing graph keeps
    its current depth and stays byte-identical):

    * CROSSING consumers (VALUE edge into a crossing): need ``s + 2`` bars so
      ``var[s]`` and ``var[s+1]`` are both available (``s`` =
      ``barras_confirmacion``, default 1 -> depth 3). Kept VERBATIM as a special
      case.
    * Generic VALUE-edge consumers (a NON-crossing value edge): a consumer
      reading bar ``k`` needs depth ``k + 1``. No shipping non-crossing node
      consumes a value edge, so this stays inert today.
    * OPERAND consumers (a Condition referencing ``<var>[k]`` or the named
      ``<var>:<out>[shift]`` form): the read is resolved OUTPUT-AWARE via
      :func:`_operand_read_index`, so a plain ``[0]`` keeps depth 1 while a deeper
      read (e.g. RSI ``:prev`` at ``bar_shift`` -> real index ``bar_shift+1``)
      bumps depth to ``bar_shift + 2``. Plain ``[0]`` operands stay inert.

    Indicators with no consumer keep the implicit depth 1 (absent from the dict).
    """
    by_id: dict[str, Node] = {str(n.get("id", "")): n for n in nodes}
    depth: dict[str, int] = {}

    def bump(src_id: str, want: int) -> None:
        if want > 1:
            depth[src_id] = max(depth.get(src_id, 1), want)

    for edge in edges:
        src_id = str(edge.get("source", ""))
        src_node = by_id.get(src_id)
        tgt_node = by_id.get(str(edge.get("target", "")))
        if tgt_node is None or src_node is None:
            continue
        if buffer_var(src_node) is None:
            continue  # only SMA/RSI/MACD have a copyable single buffer
        if edge_role(str(edge.get("targetHandle") or "")) != "VALUE":
            continue
        if resolve_node_type(tgt_node).lower() in _CROSSING_TYPES:
            # Crossing special case — VERBATIM s+2 (default 1 -> depth 3).
            s = int(param(tgt_node, "barras_confirmacion", 1))
            bump(src_id, max(1, s + 2))
        else:
            # Generic value-edge consumer: bar k -> depth k+1 (DORMANT; no
            # shipping non-crossing node consumes a value edge today).
            k = int(param(tgt_node, "value_bar", 0))
            bump(src_id, k + 1)

    # Operand consumers: a Condition operand string reading <var>[k] needs
    # depth k+1 (DORMANT; every shipping operand reads [0]).
    for node in nodes:
        if resolve_node_type(node).lower() != "condition":
            continue
        for src_node in nodes:
            var = buffer_var(src_node)
            if var is None:
                continue
            src_id = str(src_node.get("id", ""))
            for key in ("left", "right"):
                operand = param(node, key, "")
                bump(src_id, _operand_read_index(operand, src_node, var) + 1)

    _zscore_copy_depth(nodes, edges, by_id, bump)
    return depth


def _zscore_copy_depth(
    nodes: list[Node],
    edges: list[Edge],
    by_id: dict[str, Node],
    bump: Any,
) -> None:
    """Fold ZScore depth into the running ``depth`` map via ``bump``.

    ZScore is keyed by NODE ID (one depth applied to all three of its arrays —
    zmean/zstd/zsma — since ``nodes/zscore.py`` reads a single ``depth_for(id)``).
    Three contributions, max-wins:

    * SELF read: the node itself reads bar ``mu_idx`` (= ``desplazamiento_barra``
      inclusive, ``+1`` exclusive), so it always needs ``mu_idx + 1`` bars.
    * CROSSING consumer of a ZScore ARRAY output (zmean/zstd/zsma value edge into
      a crossing): needs ``s + 2`` so ``[s]`` and ``[s+1]`` are available.
    * OPERAND consumer: a Condition referencing ``z{mean,std,sma}_<id>[k]`` needs
      ``k + 1``.
    """
    for node in nodes:
        if resolve_node_type(node).lower() != "zscore":
            continue
        nid = str(node.get("id", ""))
        ventana = param(node, "ventana_mu_sigma", "inclusive")
        b = int(param(node, "desplazamiento_barra", 0))
        mu_idx = b + 1 if ventana == "exclusive" else b
        bump(nid, mu_idx + 1)  # self read

    # Crossing consumers of a ZScore array output.
    for edge in edges:
        src_node = by_id.get(str(edge.get("source", "")))
        tgt_node = by_id.get(str(edge.get("target", "")))
        if src_node is None or tgt_node is None:
            continue
        if resolve_node_type(src_node).lower() != "zscore":
            continue
        if edge_role(str(edge.get("targetHandle") or "")) != "VALUE":
            continue
        src_handle = str(edge.get("sourceHandle") or "")
        if src_handle not in ZSCORE_ARRAY_OUTPUTS:
            continue  # scalar/signal outputs are not array-indexed
        if resolve_node_type(tgt_node).lower() in _CROSSING_TYPES:
            s = int(param(tgt_node, "barras_confirmacion", 1))
            bump(str(src_node.get("id", "")), max(1, s + 2))

    # Operand consumers: a Condition referencing a ZScore array output read.
    zscore_ids = {
        str(n.get("id", ""))
        for n in nodes
        if resolve_node_type(n).lower() == "zscore"
    }
    for node in nodes:
        if resolve_node_type(node).lower() != "condition":
            continue
        for key in ("left", "right"):
            operand = str(param(node, key, ""))
            for zid in zscore_ids:
                for arr_prefix in ("zmean_", "zstd_", "zsma_"):
                    var = f"{arr_prefix}{zid}"
                    bump(zid, _operand_shift(operand, var) + 1)
