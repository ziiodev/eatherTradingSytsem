"""SMA node — registers a moving-average series in compute_indicators."""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param, py_id
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "SMA"

# MQL5 ma_method -> stdlib indicator helper. SMA/EMA are supported; any other
# method falls back to the simple moving average.
_MA_FN = {"MODE_SMA": "ind_sma", "MODE_EMA": "ind_ema"}


def generate(node: Node, connections: Connections) -> str:
    """Compute ``sma_<id>`` over the applied price; emit an on_bar landmark."""
    period = param(node, "period", 14)
    applied_price = param(node, "applied_price", "PRICE_CLOSE")
    ma_method = str(param(node, "ma_method", "MODE_SMA"))
    fn = _MA_FN.get(ma_method, "ind_sma")
    nid = py_id(node)
    var = f"sma_{nid}"
    connections.context.add_compute(
        var,
        f'{IND}ind["{var}"] = {fn}(ind_price(bars, "{applied_price}"), {period})',
    )
    return f"{comment(node, NODE_TYPE)}\n{IND}# series ind[\"{var}\"] ready"
