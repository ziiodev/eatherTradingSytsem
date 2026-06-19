"""Python dispatcher — assembles a standalone stdlib script from a node graph.

Mirrors the MQL5 ``generator.py`` flow: it REUSES the shared graph layer
(ordering, guard placement, value-indicator hoisting, copy-depth) and asks each
registered Python per-node generator for its ``on_bar`` snippet, wrapping
everything in the pure-stdlib script skeleton.

Two emission streams are collected per render:

* ``on_bar`` body — returned by each node's ``generate(node, connections)``.
* ``compute_indicators`` body — registered by indicator nodes via
  ``ctx.add_compute(...)`` so each indicator series is computed exactly once.
"""

from __future__ import annotations

from typing import Any

from aether_api.services.codegen.copy_depth import _indicator_copy_depth
from aether_api.services.codegen.graph import (
    _combinator_fed_condition_ids,
    _hoist_value_indicators,
    _order_nodes,
    resolve_node_type,
)
from aether_api.services.codegen.helpers import param
from aether_api.services.codegen.python.indicators import INDICATOR_LIB
from aether_api.services.codegen.python.registry import get_generator
from aether_api.services.codegen.python.skeleton import render_script
from aether_api.services.codegen.types import Connections, Node, RenderContext

_INDENT = "    "


def _render_body(graph: dict[str, Any]) -> tuple[str, str]:
    """Render ``(on_bar_body, compute_body)`` for the graph.

    A pre-render scan records the RiskManagement fact + the copy-depth map (the
    Python indicators use depth only for symmetry; warmup is implicit in the
    aligned series). Unknown node types are skipped with a ``#`` comment marker,
    mirroring the MQL5 engine.
    """
    nodes: list[Node] = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))

    ctx = RenderContext()
    ctx.nodes_by_id = {str(n.get("id", "")): n for n in nodes}
    for node in nodes:
        if resolve_node_type(node).lower() == "riskmanagement":
            ctx.has_risk_node = True
            ctx.risk_percent = param(node, "risk_percent", 1.0)
            break
    ctx.copy_depth = _indicator_copy_depth(nodes, edges)

    connections = Connections(edges, context=ctx)
    excluded_ids = _combinator_fed_condition_ids(nodes, edges)
    ordered = _hoist_value_indicators(
        _order_nodes(nodes, connections, excluded_ids), edges
    )

    lines: list[str] = []
    for node in ordered:
        ntype = resolve_node_type(node)
        generator = get_generator(ntype)
        if generator is None:
            lines.append(f"{_INDENT}# [unknown node type '{ntype}'] skipped")
            continue
        snippet = generator(node, connections)
        if snippet:
            lines.append(snippet)
    on_bar_body = "\n".join(lines) if lines else f"{_INDENT}# (empty strategy)"
    return on_bar_body, ctx.compute_block()


def generate_python(graph: dict[str, Any], ea_name: str = "GeneratedEA") -> str:
    """Generate a complete standalone Python script from a serialized graph."""
    on_bar_body, compute_body = _render_body(graph)
    return render_script(
        ea_name=ea_name,
        compute_body=compute_body,
        on_bar_body=on_bar_body,
        indicator_lib=INDICATOR_LIB,
    )
