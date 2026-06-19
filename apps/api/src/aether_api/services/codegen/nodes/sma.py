"""SMA node — declares a Simple Moving Average indicator handle + buffer read."""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    comment_header,
    copy_buffer_lines,
    node_id,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "SMA"


def generate(node: Node, connections: Connections) -> str:
    """Emit an iMA-based SMA value read into a local variable.

    The CopyBuffer depth defaults to 1 (single newest bar, byte-identical to the
    legacy output); a downstream crossing raises it via ``ctx.copy_depth`` so the
    previous bar(s) are available.
    """
    period = param(node, "period", 14)
    shift = param(node, "shift", 0)
    ma_method = param(node, "ma_method", "MODE_SMA")
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    nid = node_id(node)
    var = f"sma_{nid}"
    depth = connections.context.depth_for(nid)
    ima = (
        f"iMA(_Symbol, _Period, {period}, {shift}, {ma_method}, {applied_price})"
    )
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   double {var}[];\n"
        f"   int h_{var} = {ima};\n"
        f"{copy_buffer_lines(f'h_{var}', '0', var, depth)}"
    )
