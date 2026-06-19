"""MACD node — registers a MACD main-line series in compute_indicators."""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param, py_id
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "MACD"


def generate(node: Node, connections: Connections) -> str:
    """Compute ``macd_<id>`` (fast EMA - slow EMA); emit an on_bar landmark."""
    fast = param(node, "fast_ema", 12)
    slow = param(node, "slow_ema", 26)
    signal = param(node, "signal", 9)
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    nid = py_id(node)
    var = f"macd_{nid}"
    connections.context.add_compute(
        var,
        f'{IND}ind["{var}"] = ind_macd('
        f'ind_price(bars, "{applied_price}"), {fast}, {slow}, {signal})',
    )
    return f"{comment(node, NODE_TYPE)}\n{IND}# series ind[\"{var}\"] ready"
