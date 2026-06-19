"""Language-agnostic graph layer shared by the MQL5 and Python codegen engines.

This module holds the part of the codegen pipeline that has NOTHING to do with the
target language: how nodes are typed, ordered (DFS from Start following flow
edges), how off-chain GUARD nodes (combinators / crossings) are placed by their
OUTPUT flow edge, how off-chain value-source indicators are hoisted before the
guard that reads them, and how operand strings expose a bar shift / read index.

Both the MQL5 dispatcher (``generator.py``) and the Python dispatcher
(``python/generator.py``) import these helpers, so the two engines agree on the
exact emission order and topology semantics. The MQL5 generator re-exports every
public name here so existing imports across the codebase stay unchanged.

The copy-depth scan (which is also language-agnostic — Python needs the same
warmup map) lives in the sibling :mod:`copy_depth` module to keep both files
under the ~150-line limit.
"""

from __future__ import annotations

from aether_api.services.codegen.buffer_ref import resolve_output
from aether_api.services.codegen.types import Connections, Edge, Node, edge_role

# Domain types that fan in upstream Condition expressions. A Condition feeding
# any of these is rendered by the combinator (off-chain) rather than emitting its
# own standalone guard.
_COMBINATOR_TYPES = {"logicaland", "logicalor", "logicalnot", "logicalxor"}

# Crossing node domain types. They consume indicator OUTPUTS through VALUE edges
# (value1/value2) and need the previous bar, driving the copy-depth bump.
_CROSSING_TYPES = {"bullishcross", "bearishcross"}

# GUARD node domain types: every node that emits an early guard yet has NO flow-IN
# handle (combinators consume CONDITION edges, crossings consume VALUE edges). The
# Start DFS therefore never reaches them, so they must be placed by their OUTPUT
# flow edge (the node they gate) rather than appended in node-insertion order.
# This union is the single codegen source of truth for "is this node a guard?".
_GUARD_TYPES = _COMBINATOR_TYPES | _CROSSING_TYPES


def resolve_node_type(node: Node) -> str:
    """Resolve a node's logical (domain) type.

    Real React-Flow editor nodes are shaped ``{type: "custom", data: {type:
    "<NodeType>"}}`` — the top-level ``type`` is just the renderer key. When the
    top-level ``type`` is missing, empty, or equal to ``"custom"`` (case-
    insensitive), fall through to ``data.type``. Legacy/test graphs that carry
    the real type at the top level (e.g. ``{type: "RSI"}``) are still honored.
    """
    top = str(node.get("type") or "")
    if top and top.lower() != "custom":
        return top
    data = node.get("data") or {}
    return str(data.get("type", ""))


def _combinator_fed_condition_ids(
    nodes: list[Node], edges: list[Edge]
) -> set[str]:
    """Return ids of Condition nodes that SOURCE an edge into a combinator.

    Those Conditions are consumed (inlined) by the combinator generator, so the
    dispatcher must NOT also emit their standalone guard — that would gate the
    chain twice. Standalone/on-chain Conditions (not feeding a combinator) are
    untouched, keeping existing graphs byte-identical.
    """
    by_id: dict[str, Node] = {str(n.get("id", "")): n for n in nodes}
    excluded: set[str] = set()
    for edge in edges:
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        tgt_node = by_id.get(tgt)
        src_node = by_id.get(src)
        if tgt_node is None or src_node is None:
            continue
        if resolve_node_type(tgt_node).lower() not in _COMBINATOR_TYPES:
            continue
        if resolve_node_type(src_node).lower() == "condition":
            excluded.add(src)
    return excluded


def _operand_shift(operand: object, var: str) -> int:
    """Return the bar shift an operand STRING reads from ``var``, else 0.

    Recognizes ``<var>[k]`` and ``<var>:<output>[k]`` (the resolver grammar) and
    returns ``k``. Anything else — a literal, a different var, a malformed
    operand — yields 0.
    """
    text = str(operand)
    marker = var + "["
    named = var + ":"
    if marker in text:
        rest = text.split(marker, 1)[1]
    elif named in text and "[" in text:
        rest = text.split("[", 1)[1]
    else:
        return 0
    idx = rest.split("]", 1)[0]
    try:
        return int(idx)
    except ValueError:
        return 0


def _operand_read_index(operand: object, src_node: Node, var: str) -> int:
    """Return the ACTUAL bar index an operand reads from ``var``, output-aware.

    Plain refs (``<var>[k]``) read bar ``k`` — identical to ``_operand_shift``.
    A NAMED ref (``<var>:<output>[shift]``) is routed through
    :func:`resolve_output`, so an output that reads further back than its written
    shift (e.g. RSI ``prev`` -> ``<var>[shift+1]``) reports the deeper index. The
    returned index drives copy-depth (``index + 1``). A literal / different var /
    unresolvable operand yields 0, so plain ``[0]`` refs stay at depth 1.
    """
    text = str(operand)
    named = var + ":"
    if named in text and "[" in text:
        # Carry the :outputId form to the resolver so the real read index
        # (which may be deeper than the written shift) is honored.
        output = text.split(named, 1)[1].split("[", 1)[0]
        shift = _operand_shift(text, var)
        resolved = resolve_output(src_node, output, shift)
        if resolved is not None:
            return _operand_shift(resolved, var)
    return _operand_shift(text, var)


def _order_nodes(
    nodes: list[Node],
    connections: Connections,
    excluded_ids: set[str] | None = None,
) -> list[Node]:
    """Return nodes ordered from the Start node following outgoing edges.

    Falls back to input order for any nodes not reachable from a Start node so
    nothing is silently dropped. Cycle-safe via a visited set.

    ``excluded_ids`` lists Condition ids that feed a combinator: they are NOT
    appended to the emission order (the combinator inlines their expression), but
    the walk STILL recurses through them so downstream nodes are unaffected.
    """
    excluded = excluded_ids or set()
    by_id: dict[str, Node] = {str(n.get("id", "")): n for n in nodes}

    def node_type(n: Node) -> str:
        return resolve_node_type(n)

    start_ids = [nid for nid, n in by_id.items() if node_type(n).lower() == "start"]
    ordered: list[Node] = []
    visited: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in visited or node_id not in by_id:
            return
        visited.add(node_id)
        # Skip APPENDING an off-chain Condition, but still recurse so any
        # downstream nodes keep their normal position in the chain.
        if node_id not in excluded:
            ordered.append(by_id[node_id])
        # Follow ONLY flow edges so a value/condition operand wire never becomes
        # the chain. For legacy graphs (no handles) this equals ``outgoing``.
        for nxt in connections.outgoing_flow(node_id):
            walk(nxt)

    for sid in start_ids:
        walk(sid)

    # Partition the still-unreachable, non-excluded nodes into GUARD nodes and
    # everything else. GUARD nodes (combinators + crossings) have no flow-IN
    # handle, so the DFS above never reached them; placing them in insertion
    # order can emit their early-return AFTER the Buy/Sell they gate.
    unreachable = [
        (nid, node)
        for nid, node in by_id.items()
        if nid not in visited and nid not in excluded
    ]
    guards = [
        (nid, node)
        for nid, node in unreachable
        if node_type(node).lower() in _GUARD_TYPES
    ]
    non_guards = [
        (nid, node)
        for nid, node in unreachable
        if node_type(node).lower() not in _GUARD_TYPES
    ]

    # Non-guard unreachable nodes keep their original behavior: appended in
    # insertion order so nothing is silently dropped.
    for _nid, node in non_guards:
        ordered.append(node)

    # Place each unreachable guard immediately BEFORE the node its OUTPUT flow
    # edge targets (the gated node), so the early-return precedes the trade.
    _place_guards(guards, by_id, connections, ordered)
    return ordered


def _place_guards(
    guards: list[tuple[str, Node]],
    by_id: dict[str, Node],
    connections: Connections,
    ordered: list[Node],
) -> None:
    """Splice unreachable GUARD nodes into ``ordered`` by their OUTPUT flow edge.

    Each guard is inserted immediately BEFORE the node its ``outgoing_flow``
    target resolves to (the node it gates), so its early-return precedes that
    node's emission. Guard CHAINS (a guard whose output targets another guard)
    are handled with cycle-safe recursive placement: the deepest guard is
    inserted first and every guard ends up before the guard/node it gates.

    A guard with NO output flow edge is appended at the tail (never dropped).
    Each guard is inserted EXACTLY once (marked placed before recursing, which
    also breaks any output-edge cycle). Deterministic: independent of node-
    insertion order; ties resolve by edge index via ``outgoing_flow``.
    """
    guard_ids = {nid for nid, _ in guards}
    guard_node = dict(guards)
    placed: set[str] = set()

    def insert_before(target_node: Node, node: Node) -> None:
        """Insert ``node`` into ``ordered`` just before ``target_node``."""
        try:
            idx = ordered.index(target_node)
        except ValueError:
            ordered.append(node)
            return
        ordered.insert(idx, node)

    def place(guard_id: str) -> None:
        if guard_id in placed:
            return
        placed.add(guard_id)  # mark before recursing -> exactly once + cycle-safe
        node = guard_node[guard_id]
        targets = connections.outgoing_flow(guard_id)
        target_id = targets[0] if targets else None
        if target_id is None:
            ordered.append(node)  # no output edge -> tail, never dropped
            return
        # If the gated node is itself an unreachable guard, place it FIRST so the
        # current guard can be inserted just before it (deepest emits first).
        if target_id in guard_ids:
            place(target_id)
        target_node = by_id.get(target_id)
        if target_node is None:
            ordered.append(node)
            return
        insert_before(target_node, node)

    for gid, _ in guards:
        place(gid)


def _hoist_value_indicators(
    ordered: list[Node], edges: list[Edge]
) -> list[Node]:
    """Reorder so a guard's off-chain indicator sources emit BEFORE the guard.

    Indicators that sit on the flow chain already emit upstream of the
    (downstream) guard, so nothing moves for them. An indicator that is ONLY
    operand-connected (off the flow chain) would otherwise be appended after the
    guard as an unreachable node; this hoists it to just before its consuming
    guard so its buffer var / computed scalar is declared before the guard
    references it. Two guard families pull their operand sources forward:

    * CROSSING (VALUE edge value1/value2): ANY value-source node (SMA/RSI/MACD or
      a ZScore array output). Crossing-free graphs are untouched.
    * COMBINATOR (CONDITION edge cond1..cond6) fed by a ZScore SIGNAL output: the
      ZScore node must emit before the combinator guard reads ``z_<id>``. (A
      Condition feeding a combinator is INLINED, not emitted, so it never needs
      hoisting — only a real ZScore node does.)

    Graphs with neither pattern stay byte-identical (the maps stay empty).
    """
    by_id: dict[str, Node] = {str(n.get("id", "")): n for n in ordered}
    # Map guard id -> ordered list of its operand-source ids to hoist.
    value_sources: dict[str, list[str]] = {}
    for edge in edges:
        tgt = str(edge.get("target", ""))
        tgt_node = by_id.get(tgt)
        if tgt_node is None:
            continue
        role = edge_role(str(edge.get("targetHandle") or ""))
        tgt_type = resolve_node_type(tgt_node).lower()
        src_id = str(edge.get("source", ""))
        if role == "VALUE" and tgt_type in _CROSSING_TYPES:
            value_sources.setdefault(tgt, []).append(src_id)
        elif role == "CONDITION" and tgt_type in _COMBINATOR_TYPES:
            # Only a real (emitted) ZScore source needs hoisting; an inlined
            # Condition source is never in ``ordered`` to begin with.
            src_node = by_id.get(src_id)
            if src_node is not None and resolve_node_type(src_node).lower() == "zscore":
                value_sources.setdefault(tgt, []).append(src_id)
    if not value_sources:
        return ordered

    result: list[Node] = []
    emitted: set[str] = set()
    for node in ordered:
        nid = str(node.get("id", ""))
        if nid in emitted:
            continue
        # Before a crossing, splice in any value-source indicator not yet emitted.
        for src_id in value_sources.get(nid, []):
            if src_id not in emitted and src_id in by_id:
                result.append(by_id[src_id])
                emitted.add(src_id)
        result.append(node)
        emitted.add(nid)
    return result
