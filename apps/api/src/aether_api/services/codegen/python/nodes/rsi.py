"""RSI node — registers a Wilder RSI series in compute_indicators."""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param, py_id
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "RSI"


def generate(node: Node, connections: Connections) -> str:
    """Compute ``rsi_<id>`` over the applied price; emit an on_bar landmark."""
    period = param(node, "period", 14)
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    nid = py_id(node)
    var = f"rsi_{nid}"
    connections.context.add_compute(
        var,
        f'{IND}ind["{var}"] = ind_rsi(ind_price(bars, "{applied_price}"), {period})',
    )
    return f"{comment(node, NODE_TYPE)}\n{IND}# series ind[\"{var}\"] ready"
