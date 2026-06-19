"""Shared types for the codegen engine."""

from __future__ import annotations

from typing import Any, Protocol

# A node is the raw dict coming from the serialized React Flow graph.
Node = dict[str, Any]
# An edge connects a source node to a target node.
Edge = dict[str, Any]

# Edge-role classification by ``targetHandle``. A FLOW edge is the linear chain
# (no/empty target handle); VALUE edges feed a crossing's value1/value2 inputs;
# CONDITION edges feed a combinator's cond1..cond6 inputs. These mirror the
# frontend handle ids exactly so the two sides classify edges identically.
_VALUE_HANDLES = frozenset({"value1", "value2"})
_CONDITION_HANDLES = frozenset({f"cond{i}" for i in range(1, 7)})


def edge_role(target_handle: str | None) -> str:
    """Classify an edge by its ``targetHandle`` into FLOW / VALUE / CONDITION.

    FLOW = the primary id-less chain input (handle null/empty); VALUE =
    value1/value2 (crossing operands); CONDITION = cond1..cond6 (combinator
    operands). Anything unrecognized is treated as FLOW so legacy graphs (which
    never carried handles) keep their existing linear behavior.
    """
    handle = target_handle or ""
    if handle in _VALUE_HANDLES:
        return "VALUE"
    if handle in _CONDITION_HANDLES:
        return "CONDITION"
    return "FLOW"


class RenderContext:
    """Graph-level facts + file-scope helper registry shared across nodes.

    Built once per render and threaded through :class:`Connections` so per-node
    generators can (a) learn graph-wide facts (e.g. whether a RiskManagement
    node exists and its risk percent) and (b) contribute deduplicated
    file-scope helper functions that the generator splices into the EA once.

    The helper registry is keyed by helper name; registering the same name more
    than once is idempotent (first source wins), so a helper is emitted exactly
    once regardless of how many nodes request it.
    """

    def __init__(
        self, *, has_risk_node: bool = False, risk_percent: float = 1.0
    ) -> None:
        self.has_risk_node = has_risk_node
        self.risk_percent = risk_percent
        # Additive node-by-id lookup so a generator (e.g. a boolean combinator)
        # can resolve incoming node ids -> node dicts. Populated once per render
        # by the dispatcher; empty by default so nothing depends on it.
        self.nodes_by_id: dict[str, Node] = {}
        # Per-indicator CopyBuffer depth (number of bars to copy). Default depth
        # is 1 (current byte-identical behavior); a crossing consuming an
        # indicator raises it to s+2 so var[s] and var[s+1] are available. Keyed
        # by indicator node id; absent ids default to 1.
        self.copy_depth: dict[str, int] = {}
        self._helpers: dict[str, str] = {}
        self._prologue: dict[str, None] = {}
        # Python-engine ONLY: ordered, deduped ``compute_indicators`` body lines
        # contributed by indicator nodes. Keyed by the series var name so the
        # same indicator is computed exactly once. Empty + unused by the MQL5
        # engine, so it is fully additive (MQL5 output stays byte-identical).
        self._compute: dict[str, str] = {}

    def depth_for(self, node_id: str) -> int:
        """Return the CopyBuffer depth for an indicator id (default 1)."""
        return self.copy_depth.get(node_id, 1)

    def add_helper(self, name: str, src: str) -> None:
        """Register a file-scope helper by name (idempotent — first wins)."""
        if name not in self._helpers:
            self._helpers[name] = src

    def helpers_block(self) -> str:
        """Return all registered helper sources concatenated, or "" if none.

        The empty-string return is load-bearing: when no helper is registered
        the generator must splice ZERO bytes so existing graphs stay
        byte-identical.
        """
        if not self._helpers:
            return ""
        return "\n".join(self._helpers.values())

    def add_prologue(self, line: str) -> None:
        """Register an OnTick-prologue line (idempotent — deduped by exact text).

        Prologue lines run at the very top of ``OnTick`` (before the DFS body and
        every entry guard), so position-management calls like ``ManageTrailing``
        execute ahead of any early-return. Keyed by the exact call string so a
        Buy and Sell emitting the character-identical call coalesce to one line.
        """
        if line not in self._prologue:
            self._prologue[line] = None

    def prologue_block(self) -> str:
        """Return all registered prologue lines joined, or "" if none.

        The empty-string return is load-bearing: when no prologue is registered
        the generator splices ZERO bytes so existing graphs stay byte-identical.
        """
        if not self._prologue:
            return ""
        return "\n".join(self._prologue.keys())

    def add_compute(self, key: str, src: str) -> None:
        """Register a ``compute_indicators`` body line by series key (first wins).

        Python engine only. Idempotent per ``key`` so an indicator referenced
        from several places computes its series exactly once.
        """
        if key not in self._compute:
            self._compute[key] = src

    def compute_block(self) -> str:
        """Return all registered compute lines joined, or "" if none."""
        if not self._compute:
            return ""
        return "\n".join(self._compute.values())


class Connections:
    """Lookup helper exposing a node's inbound/outbound neighbours.

    Built once per graph and passed to every node generator so each module can
    reason about its local context without re-scanning the whole edge list.

    Also carries the optional shared :class:`RenderContext` so per-node
    generators can reach graph-level facts and the helper registry without
    changing the ``generate(node, connections)`` contract.
    """

    def __init__(
        self, edges: list[Edge], context: RenderContext | None = None
    ) -> None:
        self._outgoing: dict[str, list[str]] = {}
        self._incoming: dict[str, list[str]] = {}
        # Parallel handle-aware map: per target node, the inbound edges as
        # ``(source_id, target_handle, source_handle)`` tuples in edge order.
        # Additive — it does NOT alter ``incoming()``/``outgoing()`` so existing
        # callers are byte-identical. ``source_handle`` is captured ADDITIVELY
        # (absent ⇒ "" ⇒ primary output) so a future multi-output indicator can
        # carry WHICH output a value-edge taps; it is dormant for legacy graphs
        # that never set ``sourceHandle``. ``_flow_out`` records only FLOW
        # outgoing targets.
        self._incoming_handled: dict[str, list[tuple[str, str, str]]] = {}
        self._flow_out: dict[str, list[str]] = {}
        self.context = context if context is not None else RenderContext()
        for edge in edges:
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            if not src or not tgt:
                continue
            self._outgoing.setdefault(src, []).append(tgt)
            self._incoming.setdefault(tgt, []).append(src)
            raw_handle = edge.get("targetHandle")
            handle = "" if raw_handle is None else str(raw_handle)
            raw_src_handle = edge.get("sourceHandle")
            src_handle = "" if raw_src_handle is None else str(raw_src_handle)
            self._incoming_handled.setdefault(tgt, []).append(
                (src, handle, src_handle)
            )
            if edge_role(handle) == "FLOW":
                self._flow_out.setdefault(src, []).append(tgt)

    def outgoing(self, node_id: str) -> list[str]:
        """Return ids of nodes this node points to."""
        return self._outgoing.get(node_id, [])

    def incoming(self, node_id: str) -> list[str]:
        """Return ids of nodes pointing to this node."""
        return self._incoming.get(node_id, [])

    def outgoing_flow(self, node_id: str) -> list[str]:
        """Return ids reached from ``node_id`` by a FLOW edge only.

        FLOW edges are the linear chain (target handle null/empty). Value and
        condition edges are excluded so the DFS walk that builds the emission
        order never follows an operand wire. When no edge carried a handle (the
        legacy/common case), every outgoing edge is FLOW, so this equals
        :meth:`outgoing`.
        """
        return self._flow_out.get(node_id, [])

    def incoming_handled(
        self, node_id: str
    ) -> list[tuple[str, str, str]]:
        """Return ``(source_id, target_handle, source_handle)`` for inbound edges.

        Order follows edge insertion order. ``source_handle`` is ``""`` when the
        edge carried none (the legacy/common case ⇒ the upstream's PRIMARY
        output). Used by combinator/crossing plumbing that must know WHICH
        upstream output each operand edge taps.
        """
        return list(self._incoming_handled.get(node_id, []))

    def incoming_on_handle(self, node_id: str, handle_id: str) -> list[str]:
        """Return source ids whose edge targets ``node_id`` on ``handle_id``.

        Used by crossing nodes to resolve their value1/value2 operand sources.
        Order follows edge insertion order.
        """
        return [
            src
            for src, handle, _src_handle in self._incoming_handled.get(node_id, [])
            if handle == handle_id
        ]

    def incoming_source_handle(
        self, node_id: str, handle_id: str
    ) -> str | None:
        """Return the ``sourceHandle`` of the edge into ``node_id`` on ``handle_id``.

        Resolves WHICH upstream OUTPUT a value-edge taps. Returns ``None`` when
        the edge carried no ``sourceHandle`` (the legacy/common case — an absent
        source handle means the upstream's PRIMARY output) or when no such edge
        exists. The first matching edge in insertion order wins. This is the
        carriage a future multi-output indicator uses to feed ``resolve_output``;
        it is dormant for every shipping graph.
        """
        for _src, handle, src_handle in self._incoming_handled.get(node_id, []):
            if handle == handle_id:
                return src_handle or None
        return None


class NodeGenerator(Protocol):
    """Contract every per-node codegen module must satisfy."""

    def generate(self, node: Node, connections: Connections) -> str:
        """Return the MQL5 snippet for `node`."""
        ...
