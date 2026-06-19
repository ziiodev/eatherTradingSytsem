"""ZScore node — registers the computed Z series plus mean/std/sma arrays.

Mirrors ``nodes/zscore.py`` semantics: rolling mean (μ) and population standard
deviation (σ) over ``periodo_ventana`` bars, a longer SMA over ``barras_sma``, and
the computed scalar Z = (price - μ) / σ per bar (Z = 0 when σ == 0). When
``desviacion_estandar == "sample"`` the Bessel correction is applied (the helper
multiplies σ by sqrt(N / (N - 1))), matching the MQL5 branch.

Four series are registered, keyed to the node id and named to match the operand
prefixes: ``z_<id>`` (scalar), ``zmean_<id>``, ``zstd_<id>``, ``zsma_<id>``.
"""

from __future__ import annotations

from aether_api.services.codegen.python.pyhelpers import IND, comment, param, py_id
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "ZScore"


def generate(node: Node, connections: Connections) -> str:
    """Compute the z/mean/std/sma series for this ZScore node."""
    window = param(node, "periodo_ventana", 20)
    sma_period = param(node, "barras_sma", 500)
    applied_price = param(node, "precio_aplicado", "PRICE_CLOSE")
    sample = "True" if param(node, "desviacion_estandar", "sample") == "sample" else "False"
    nid = py_id(node)
    z_var = f"z_{nid}"
    mean_var = f"zmean_{nid}"
    std_var = f"zstd_{nid}"
    sma_var = f"zsma_{nid}"
    connections.context.add_compute(
        z_var,
        f'{IND}ind["{z_var}"], ind["{mean_var}"], ind["{std_var}"], ind["{sma_var}"] = (\n'
        f'{IND}{IND}ind_zscore(ind_price(bars, "{applied_price}"), '
        f"{window}, {sma_period}, sample={sample})\n"
        f"{IND})",
    )
    return f"{comment(node, NODE_TYPE)}\n{IND}# series ind[\"{z_var}\"] (+mean/std/sma) ready"
