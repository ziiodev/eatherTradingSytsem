"""Stochastic node — declares a Stochastic Oscillator (%K main, %D signal) read."""

from __future__ import annotations

from aether_api.services.codegen.helpers import comment_header, node_id, param
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Stochastic"


def generate(node: Node, connections: Connections) -> str:
    """Emit an iStochastic handle plus %K (main) and %D (signal) value reads.

    Two output buffers are copied into ``stochk_<id>`` (MAIN_LINE -> %K) and
    ``stochd_<id>`` (SIGNAL_LINE -> %D); these variable names are a parity
    contract with the frontend operand prefixes ``stochk_`` / ``stochd_``.
    """
    k_period = param(node, "k_period", 14)
    d_period = param(node, "d_period", 3)
    slowing = param(node, "slowing", 3)
    ma_method = param(node, "ma_method", "MODE_SMA")
    price_field = param(node, "price_field", "STO_LOWHIGH")
    nid = node_id(node)
    k_var = f"stochk_{nid}"
    d_var = f"stochd_{nid}"
    istoch = (
        f"iStochastic(_Symbol, _Period, {k_period}, {d_period}, {slowing}, "
        f"{ma_method}, {price_field})"
    )
    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   double {k_var}[];\n"
        f"   double {d_var}[];\n"
        f"   int h_stoch_{nid} = {istoch};\n"
        f"   CopyBuffer(h_stoch_{nid}, MAIN_LINE, 0, 1, {k_var});\n"
        f"   CopyBuffer(h_stoch_{nid}, SIGNAL_LINE, 0, 1, {d_var});"
    )
