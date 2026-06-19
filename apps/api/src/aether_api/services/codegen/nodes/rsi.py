"""RSI node — declares a Relative Strength Index indicator read."""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    comment_header,
    copy_buffer_lines,
    node_id,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "RSI"


def generate(node: Node, connections: Connections) -> str:
    """Emit an iRSI-based value read into a local variable.

    Depth defaults to 1 (byte-identical legacy read); a downstream crossing
    raises it via ``ctx.copy_depth`` to expose previous bar(s).
    """
    period = param(node, "period", 14)
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    nid = node_id(node)
    var = f"rsi_{nid}"
    depth = connections.context.depth_for(nid)
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   double {var}[];\n"
        f"   int h_{var} = iRSI(_Symbol, _Period, {period}, {applied_price});\n"
        f"{copy_buffer_lines(f'h_{var}', '0', var, depth)}"
    )
