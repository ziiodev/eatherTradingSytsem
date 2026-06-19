"""ZScore node — rolling Z-Score ASSEMBLED from native MQL5 pieces.

ZScore is the first indicator with NO native ``i*`` of its own. It is composed
from three native handles plus one COMPUTED local scalar:

* ``zmean_<id>``  — iMA(period=periodo_ventana, MODE_SMA) -> the rolling mean μ.
* ``zstd_<id>``   — iStdDev(period=periodo_ventana, MODE_SMA) -> the rolling σ.
* ``zsma_<id>``   — iMA(period=barras_sma, MODE_SMA) -> a longer-window SMA output.
* ``z_<id>``      — the COMPUTED scalar Z = (price - μ) / σ on the chosen bar.

It exposes 7 outputs (5 value + 2 signal):
  value : value (z primary), zabs (|z|, operand-only), zmean (μ), zstd (σ), zsma.
  signal: zgt (Z > 0), zlt (Z < 0).

The scalar ``z`` is read at ``mu_idx`` = ``desplazamiento_barra`` (inclusive) or
``+1`` (exclusive). σ from iStdDev is the POPULATION standard deviation; when
``desviacion_estandar`` == ``"sample"`` we multiply it by the Bessel correction
``MathSqrt(N / (N - 1.0))`` to convert to the sample standard deviation. A zero σ
yields ``z = 0`` so |Z| is 0 and the Z>0 / Z<0 signals are both false.
"""

from __future__ import annotations

from aether_api.services.codegen.helpers import (
    applied_price_series,
    comment_header,
    copy_buffer_lines,
    node_id,
    param,
)
from aether_api.services.codegen.types import Connections, Node

NODE_TYPE = "ZScore"


def generate(node: Node, connections: Connections) -> str:
    """Emit the 3 native handles, depth-aware copies, and the computed ``z``."""
    periodo_ventana = param(node, "periodo_ventana", 20)
    barras_sma = param(node, "barras_sma", 500)
    desplazamiento_barra = int(param(node, "desplazamiento_barra", 0))
    applied_price = param(node, "precio_aplicado", "PRICE_CLOSE")
    ventana = param(node, "ventana_mu_sigma", "inclusive")
    desviacion = param(node, "desviacion_estandar", "sample")

    nid = node_id(node)
    mean_var = f"zmean_{nid}"
    std_var = f"zstd_{nid}"
    sma_var = f"zsma_{nid}"
    depth = connections.context.depth_for(nid)

    # μ/σ read bar: the configured bar (inclusive) or one bar older (exclusive),
    # so the window that produced μ/σ excludes the bar being scored.
    mu_idx = desplazamiento_barra + 1 if ventana == "exclusive" else desplazamiento_barra

    # σ from iStdDev is the POPULATION stddev. Convert to SAMPLE stddev with the
    # Bessel factor only when requested; population stddev is used verbatim.
    sigma = f"{std_var}[{mu_idx}]"
    if desviacion == "sample":
        sigma = (
            f"{std_var}[{mu_idx}] * "
            f"MathSqrt({periodo_ventana} / ({periodo_ventana} - 1.0))"
        )

    price = applied_price_series(applied_price, str(mu_idx))

    mean_ima = (
        f"iMA(_Symbol, _Period, {periodo_ventana}, 0, MODE_SMA, {applied_price})"
    )
    std_istddev = (
        f"iStdDev(_Symbol, _Period, {periodo_ventana}, 0, MODE_SMA, "
        f"{applied_price})"
    )
    sma_ima = f"iMA(_Symbol, _Period, {barras_sma}, 0, MODE_SMA, {applied_price})"

    return (
        f"{comment_header(node, NODE_TYPE)}\n"
        f"   double {mean_var}[];\n"
        f"   int h_{mean_var} = {mean_ima};\n"
        f"{copy_buffer_lines(f'h_{mean_var}', '0', mean_var, depth)}\n"
        f"   double {std_var}[];\n"
        f"   int h_{std_var} = {std_istddev};\n"
        f"{copy_buffer_lines(f'h_{std_var}', '0', std_var, depth)}\n"
        f"   double {sma_var}[];\n"
        f"   int h_{sma_var} = {sma_ima};\n"
        f"{copy_buffer_lines(f'h_{sma_var}', '0', sma_var, depth)}\n"
        f"   double z_{nid} = ({std_var}[{mu_idx}] != 0.0) ? "
        f"({price} - {mean_var}[{mu_idx}]) / ({sigma}) : 0.0;"
    )
