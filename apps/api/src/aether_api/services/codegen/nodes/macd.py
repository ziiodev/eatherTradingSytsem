"""MACD node — declares a Moving Average Convergence Divergence read."""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    comment_header,
    copy_buffer_lines,
    node_id,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "MACD"


def generate(node: Node, connections: Connections) -> str:
    """Emit an iMACD-based main-line value read into a local variable.

    Depth defaults to 1 (byte-identical legacy read); a downstream crossing
    raises it via ``ctx.copy_depth`` to expose previous bar(s).
    """
    fast = param(node, "fast_ema", 12)
    slow = param(node, "slow_ema", 26)
    signal = param(node, "signal", 9)
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    nid = node_id(node)
    var = f"macd_{nid}"
    depth = connections.context.depth_for(nid)
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   double {var}[];\n"
        f"   int h_{var} = iMACD(_Symbol, _Period, {fast}, {slow}, {signal}, {applied_price});\n"
        f"{copy_buffer_lines(f'h_{var}', 'MAIN_LINE', var, depth)}"
    )
