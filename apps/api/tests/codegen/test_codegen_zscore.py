"""ZScore codegen + multi-output resolution + signal/crossing wiring tests.

ZScore is the first CUSTOM-COMPUTED multi-output indicator: assembled from three
native handles (iMA μ, iStdDev σ, a 2nd iMA SMA) plus a computed local scalar
``z``. These tests lock its emitted SHAPE and SEMANTICS:

* the 3 native handles + depth-aware CopyBuffer reads + the ``z`` guard,
* the Bessel sample/population branch and the inclusive/exclusive μ/σ bar index,
* the applied-price series read,
* the 7-output resolver (primary ``:value`` -> ``z_<id>``, |z|, μ/σ/SMA arrays,
  Z>0 / Z<0 signals),
* a ZScore SIGNAL feeding a combinator (with correct hoist order + mixed inputs),
* a ZScore ARRAY output feeding a crossing,
* the per-id copy-depth.

They are additive: the existing-node byte-identity goldens live in
``test_codegen_golden.py`` and stay untouched.
"""

from __future__ import annotations

from aether_api.services.codegen import generate_mql5
from aether_api.services.codegen.buffer_ref import resolve_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _zscore_node(node_id: str = "Z", **params: object) -> dict:
    return {"id": node_id, "type": "ZScore", "data": {**params}}


def _body(code: str) -> str:
    """Return only the OnTick body lines (between the braces)."""
    return code.split("void OnTick()\n{\n", 1)[1].rsplit("\n}\n", 1)[0]


def _simple_zscore_graph(**params: object) -> dict:
    """Start -> ZScore -> Condition(primary) -> Buy -> End."""
    return {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            _zscore_node("Z", **params),
            {
                "id": "3",
                "type": "Condition",
                "data": {"left": "zscore_Z:value[0]", "operator": ">", "right": "2.0"},
            },
            {"id": "4", "type": "Buy", "data": {"lots": 0.1}},
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "Z"},
            {"source": "Z", "target": "3"},
            {"source": "3", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }


# ---------------------------------------------------------------------------
# Codegen shape: 3 handles + z guard
# ---------------------------------------------------------------------------
def test_zscore_emits_three_native_handles() -> None:
    code = generate_mql5(_simple_zscore_graph(periodo_ventana=20, barras_sma=500))
    body = _body(code)
    assert "int h_zmean_Z = iMA(_Symbol, _Period, 20, 0, MODE_SMA, PRICE_CLOSE);" in body
    assert (
        "int h_zstd_Z = iStdDev(_Symbol, _Period, 20, 0, MODE_SMA, PRICE_CLOSE);"
        in body
    )
    assert "int h_zsma_Z = iMA(_Symbol, _Period, 500, 0, MODE_SMA, PRICE_CLOSE);" in body
    # Three CopyBuffer reads at depth 1 (no consumer), one per array.
    assert body.count("CopyBuffer(") == 3


def test_zscore_z_scalar_guard_with_sigma_zero_fallback() -> None:
    code = generate_mql5(_simple_zscore_graph(periodo_ventana=20))
    body = _body(code)
    # σ==0 ⇒ z = 0.0 (so |Z|=0 and the signals are false).
    assert (
        "double z_Z = (zstd_Z[0] != 0.0) ? "
        "(iClose(_Symbol, _Period, 0) - zmean_Z[0]) / "
        "(zstd_Z[0] * MathSqrt(20 / (20 - 1.0))) : 0.0;"
    ) in body


# ---------------------------------------------------------------------------
# Bessel factor: ON for sample, OFF for population
# ---------------------------------------------------------------------------
def test_zscore_sample_applies_bessel_factor() -> None:
    code = generate_mql5(_simple_zscore_graph(periodo_ventana=30, desviacion_estandar="sample"))
    body = _body(code)
    assert "zstd_Z[0] * MathSqrt(30 / (30 - 1.0))" in body


def test_zscore_population_omits_bessel_factor() -> None:
    code = generate_mql5(
        _simple_zscore_graph(periodo_ventana=30, desviacion_estandar="population")
    )
    body = _body(code)
    assert "MathSqrt(" not in body  # no Bessel correction for population σ
    assert "/ (zstd_Z[0]) : 0.0;" in body


# ---------------------------------------------------------------------------
# Inclusive vs exclusive μ/σ bar index
# ---------------------------------------------------------------------------
def test_zscore_inclusive_reads_bar_b() -> None:
    code = generate_mql5(
        _simple_zscore_graph(desplazamiento_barra=0, ventana_mu_sigma="inclusive")
    )
    body = _body(code)
    assert "zstd_Z[0] != 0.0" in body
    assert "zmean_Z[0]" in body
    assert "iClose(_Symbol, _Period, 0)" in body


def test_zscore_exclusive_reads_bar_b_plus_one() -> None:
    code = generate_mql5(
        _simple_zscore_graph(desplazamiento_barra=0, ventana_mu_sigma="exclusive")
    )
    body = _body(code)
    assert "zstd_Z[1] != 0.0" in body
    assert "zmean_Z[1]" in body
    assert "iClose(_Symbol, _Period, 1)" in body
    # Exclusive read at bar 1 forces self copy-depth 2.
    assert "CopyBuffer(h_zmean_Z, 0, 0, 2, zmean_Z);" in body


def test_zscore_desplazamiento_shifts_read_index() -> None:
    code = generate_mql5(
        _simple_zscore_graph(desplazamiento_barra=2, ventana_mu_sigma="inclusive")
    )
    body = _body(code)
    assert "zmean_Z[2]" in body
    assert "iClose(_Symbol, _Period, 2)" in body
    assert "CopyBuffer(h_zmean_Z, 0, 0, 3, zmean_Z);" in body  # depth = mu_idx+1


# ---------------------------------------------------------------------------
# Applied-price series mapping
# ---------------------------------------------------------------------------
def test_zscore_applied_price_median_series() -> None:
    code = generate_mql5(_simple_zscore_graph(precio_aplicado="PRICE_MEDIAN"))
    body = _body(code)
    assert (
        "((iHigh(_Symbol, _Period, 0) + iLow(_Symbol, _Period, 0)) / 2.0)" in body
    )
    # The native handles also carry the applied-price constant.
    assert "MODE_SMA, PRICE_MEDIAN)" in body


def test_zscore_applied_price_high_series() -> None:
    code = generate_mql5(_simple_zscore_graph(precio_aplicado="PRICE_HIGH"))
    body = _body(code)
    assert "iHigh(_Symbol, _Period, 0) - zmean_Z[0]" in body


# ---------------------------------------------------------------------------
# 7-output resolution
# ---------------------------------------------------------------------------
def _z(node_id: str = "Z") -> dict:
    return {"id": node_id, "type": "ZScore", "data": {}}


def test_resolve_primary_value_to_scalar() -> None:
    assert resolve_output(_z(), None, 0) == "z_Z"
    assert resolve_output(_z(), "value", 0) == "z_Z"
    # Primary ignores shift (scalar local, not an array).
    assert resolve_output(_z(), "value", 3) == "z_Z"


def test_resolve_zabs_to_mathabs() -> None:
    assert resolve_output(_z(), "zabs", 0) == "MathAbs(z_Z)"


def test_resolve_signal_outputs() -> None:
    assert resolve_output(_z(), "zgt", 0) == "(z_Z > 0)"
    assert resolve_output(_z(), "zlt", 0) == "(z_Z < 0)"


def test_resolve_array_outputs_use_shift() -> None:
    assert resolve_output(_z(), "zmean", 0) == "zmean_Z[0]"
    assert resolve_output(_z(), "zstd", 1) == "zstd_Z[1]"
    assert resolve_output(_z(), "zsma", 2) == "zsma_Z[2]"


def test_resolve_unknown_output_is_none() -> None:
    assert resolve_output(_z(), "nope", 0) is None


def test_primary_value_operand_translates_in_condition() -> None:
    code = generate_mql5(_simple_zscore_graph())
    body = _body(code)
    # The Condition operand ``zscore_Z:value[0]`` resolves to the scalar ``z_Z``.
    assert "if (!(z_Z > 2.0)) return;" in body


# ---------------------------------------------------------------------------
# Signal -> combinator (hoist order, mixed inputs)
# ---------------------------------------------------------------------------
def test_zscore_signal_into_combinator_hoisted_before_guard() -> None:
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            _zscore_node("Z"),
            {"id": "AND", "type": "LogicalAnd", "data": {"requerir_todas": True}},
            {"id": "4", "type": "Buy", "data": {"lots": 0.1}},
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "AND"},
            {
                "source": "Z",
                "target": "AND",
                "targetHandle": "cond1",
                "sourceHandle": "zgt",
            },
            {"source": "AND", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }
    body = _body(generate_mql5(graph))
    # ZScore emits (declares z_Z) BEFORE the combinator guard reads it.
    assert body.index("double z_Z =") < body.index("// [LogicalAnd]")
    assert "if (!((z_Z > 0))) return;" in body


def test_zscore_signal_mixed_with_condition_in_combinator() -> None:
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            _zscore_node("Z"),
            {
                "id": "C",
                "type": "Condition",
                "data": {"left": "1", "operator": ">", "right": "0"},
            },
            {"id": "AND", "type": "LogicalAnd", "data": {"requerir_todas": True}},
            {"id": "4", "type": "Buy", "data": {"lots": 0.1}},
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "AND"},
            {
                "source": "C",
                "target": "AND",
                "targetHandle": "cond1",
            },
            {
                "source": "Z",
                "target": "AND",
                "targetHandle": "cond2",
                "sourceHandle": "zlt",
            },
            {"source": "AND", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }
    body = _body(generate_mql5(graph))
    # Both the inlined Condition and the ZScore signal appear in one guard.
    assert "if (!((1 > 0) && (z_Z < 0))) return;" in body


# ---------------------------------------------------------------------------
# Crossing from ZScore array outputs
# ---------------------------------------------------------------------------
def test_zscore_array_outputs_feed_crossing() -> None:
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            _zscore_node("Z"),
            {"id": "X", "type": "BullishCross", "data": {}},
            {"id": "9", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "X"},
            {
                "source": "Z",
                "target": "X",
                "targetHandle": "value1",
                "sourceHandle": "zmean",
            },
            {
                "source": "Z",
                "target": "X",
                "targetHandle": "value2",
                "sourceHandle": "zstd",
            },
            {"source": "X", "target": "9"},
        ],
    }
    body = _body(generate_mql5(graph))
    # value1 -> zmean array, value2 -> zstd array, indexed [s]/[s+1] (s=1).
    assert (
        "if (!(zmean_Z[2] < zstd_Z[2] && zmean_Z[1] > zstd_Z[1])) return;" in body
    )
    # Copy depth bumped to s+2 = 3 on the consumed arrays.
    assert "CopyBuffer(h_zmean_Z, 0, 0, 3, zmean_Z);" in body
    assert "CopyBuffer(h_zstd_Z, 0, 0, 3, zstd_Z);" in body


# ---------------------------------------------------------------------------
# Copy-depth via Condition operand on a ZScore array output
# ---------------------------------------------------------------------------
def test_zscore_array_operand_bumps_copy_depth() -> None:
    graph = _simple_zscore_graph()
    # Override the Condition to read zmean at bar 3 -> depth 4 on all arrays.
    graph["nodes"][2]["data"] = {
        "left": "zmean_Z[3]",
        "operator": ">",
        "right": "0",
    }
    body = _body(generate_mql5(graph))
    assert "CopyBuffer(h_zmean_Z, 0, 0, 4, zmean_Z);" in body
    assert "CopyBuffer(h_zstd_Z, 0, 0, 4, zstd_Z);" in body
    assert "CopyBuffer(h_zsma_Z, 0, 0, 4, zsma_Z);" in body
