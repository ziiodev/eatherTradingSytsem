"""Stochastic node — registers %K (stochk_) and %D (stochd_) series.

The buffer var names ``stochk_<id>`` / ``stochd_<id>`` are a parity contract with
the frontend operand prefixes and the MQL5 engine, so a Condition referencing
``stochk_<id>[0]`` resolves to the same series here.
"""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param, py_id
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "Stochastic"


def generate(node: Node, connections: Connections) -> str:
    """Compute ``stochk_<id>`` / ``stochd_<id>``; emit an on_bar landmark."""
    k_period = param(node, "k_period", 14)
    d_period = param(node, "d_period", 3)
    slowing = param(node, "slowing", 3)
    nid = py_id(node)
    k_var = f"stochk_{nid}"
    d_var = f"stochd_{nid}"
    connections.context.add_compute(
        k_var,
        f'{IND}ind["{k_var}"], ind["{d_var}"] = ind_stochastic(\n'
        f"{IND}{IND}[b.high for b in bars], [b.low for b in bars],\n"
        f"{IND}{IND}[b.close for b in bars], {k_period}, {d_period}, {slowing},\n"
        f"{IND})",
    )
    return f"{comment(node, NODE_TYPE)}\n{IND}# series ind[\"{k_var}\"]/[\"{d_var}\"] ready"
