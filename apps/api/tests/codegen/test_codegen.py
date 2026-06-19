"""Smoke tests for the MQL5 codegen engine."""

from __future__ import annotations

import pytest
from aether_api.services.codegen import generate_mql5


def _graph() -> dict:
    """A minimal Start -> RSI -> Condition -> Buy -> End graph."""
    return {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "RSI", "data": {"period": 21}},
            {
                "id": "3",
                "type": "Condition",
                "data": {"left": "rsi_2[0]", "operator": "<", "right": "30"},
            },
            {"id": "4", "type": "Buy", "data": {"lots": 0.5}},
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }


def test_generate_contains_skeleton_and_nodes() -> None:
    code = generate_mql5(_graph(), ea_name="MyEA")
    assert "void OnTick()" in code
    assert "#include <Trade/Trade.mqh>" in code
    assert "iRSI(_Symbol" in code
    assert "trade.Buy(0.5" in code
    # The Condition node now emits a brace-free guard statement (was an
    # unclosed `if (...) {`), so the expected text changed accordingly.
    assert "if (!(rsi_2[0] < 30)) return;" in code
    assert "MyEA.mq5" in code


def test_empty_graph_is_valid() -> None:
    code = generate_mql5({"nodes": [], "edges": []})
    assert "// (empty strategy)" in code
    assert "void OnTick()" in code


def _custom_node_graph() -> dict:
    """A graph in the REAL React-Flow editor shape.

    Every node has top-level ``type == "custom"`` (the renderer key) and its
    domain type in ``data.type`` — exactly what ``graphStore.toJSON()`` emits.
    """
    return {
        "nodes": [
            {"id": "n1", "type": "custom", "data": {"label": "Start", "type": "Start"}},
            {
                "id": "n2",
                "type": "custom",
                "data": {"label": "SMA", "type": "SMA", "period": 50},
            },
            {
                "id": "n3",
                "type": "custom",
                "data": {
                    "label": "Condition",
                    "type": "Condition",
                    "left": "sma_n2[0]",
                    "operator": ">",
                    "right": "1.2345",
                },
            },
            {
                "id": "n4",
                "type": "custom",
                "data": {"label": "Buy", "type": "Buy", "lots": 0.1},
            },
            {"id": "n5", "type": "custom", "data": {"label": "End", "type": "End"}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
            {"source": "n4", "target": "n5"},
        ],
    }


def test_custom_node_shape_dispatches_every_node() -> None:
    """Real editor nodes ({type:'custom', data:{type:...}}) must dispatch."""
    code = generate_mql5(_custom_node_graph(), ea_name="CustomEA")
    assert code  # non-empty
    # No node fell through to the unknown/skipped fallback.
    assert "[unknown node type" not in code
    assert "skipped" not in code
    # The Start node was detected (its snippet only appears when ordered/walked).
    assert "// --- Strategy logic begins ---" in code
    # Per-node dispatch produced each generator's distinctive output.
    assert "iMA(_Symbol" in code  # SMA
    assert "trade.Buy(0.1" in code  # Buy


def test_condition_guard_and_balanced_braces() -> None:
    """Condition emits a brace-free guard and overall braces stay balanced."""
    code = generate_mql5(_custom_node_graph(), ea_name="CustomEA")
    assert "if (!(sma_n2[0] > 1.2345)) return;" in code
    # The flat per-node concatenation must not leave a dangling brace.
    assert code.count("{") == code.count("}")


def test_legacy_top_level_type_still_resolves() -> None:
    """Backward compat: a real type at the top level is still honored."""
    code = generate_mql5(_graph(), ea_name="Legacy")
    assert "[unknown node type" not in code
    assert "// --- Strategy logic begins ---" in code  # Start detected
    assert "iRSI(_Symbol" in code


# ---------------------------------------------------------------------------
# Phase A: indicator param substitution (ma_method / applied_price)
# ---------------------------------------------------------------------------
def test_indicator_defaults_are_byte_identical() -> None:
    """A graph with NO new keys + NO RiskManagement node keeps defaults.

    Asserts MODE_SMA / PRICE_CLOSE are present, lots stay literal, and no
    CalcLots helper leaks in.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "SMA", "data": {"period": 50}},
            {"id": "3", "type": "RSI", "data": {"period": 14}},
            {
                "id": "4",
                "type": "MACD",
                "data": {"fast_ema": 12, "slow_ema": 26, "signal": 9},
            },
            {"id": "5", "type": "Buy", "data": {"lots": 0.1}},
            {"id": "6", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
            {"source": "4", "target": "5"},
            {"source": "5", "target": "6"},
        ],
    }
    code = generate_mql5(graph, ea_name="DefEA")
    # Default indicator literals preserved.
    assert "iMA(_Symbol, _Period, 50, 0, MODE_SMA, PRICE_CLOSE)" in code
    assert "iRSI(_Symbol, _Period, 14, PRICE_CLOSE)" in code
    assert "iMACD(_Symbol, _Period, 12, 26, 9, PRICE_CLOSE)" in code
    # Literal lots, no risk-based sizing.
    assert 'trade.Buy(0.1, _Symbol, 0.0, 0, 0, "buy");' in code
    assert "CalcLots" not in code
    # Zero helper bytes spliced between CTrade and OnInit.
    assert "CTrade trade;\n\nint OnInit()" in code


def test_indicator_non_default_params_land_in_correct_slots() -> None:
    """Non-default ma_method / applied_price appear in the right iX slots."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "SMA",
                "data": {
                    "period": 50,
                    "shift": 0,
                    "ma_method": "MODE_EMA",
                    "applied_price": "PRICE_OPEN",
                },
            },
            {
                "id": "3",
                "type": "RSI",
                "data": {"period": 14, "applied_price": "PRICE_OPEN"},
            },
            {
                "id": "4",
                "type": "MACD",
                "data": {
                    "fast_ema": 12,
                    "slow_ema": 26,
                    "signal": 9,
                    "applied_price": "PRICE_OPEN",
                },
            },
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }
    code = generate_mql5(graph, ea_name="NonDefEA")
    # ma_method slot (5th arg) + applied_price slot (6th arg) of iMA.
    assert "iMA(_Symbol, _Period, 50, 0, MODE_EMA, PRICE_OPEN)" in code
    # applied_price is the 4th (last) arg of iRSI.
    assert "iRSI(_Symbol, _Period, 14, PRICE_OPEN)" in code
    # applied_price is the 6th (last) arg of iMACD.
    assert "iMACD(_Symbol, _Period, 12, 26, 9, PRICE_OPEN)" in code


# ---------------------------------------------------------------------------
# Phase B: risk-based lot sizing (CalcLots helper + sl_pips gating)
# ---------------------------------------------------------------------------
def _risk_graph(buy_data: dict) -> dict:
    """A Start -> RiskManagement -> Buy -> End graph with a tunable Buy."""
    return {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "RiskManagement",
                "data": {"max_positions": 1, "risk_percent": 2.0},
            },
            {"id": "3", "type": "Buy", "data": buy_data},
            {"id": "4", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
        ],
    }


def test_risk_present_with_sl_pips_uses_calc_lots_once() -> None:
    """RiskManagement + Buy sl_pips>0 -> CalcLots emitted ONCE, Buy uses it."""
    code = generate_mql5(_risk_graph({"sl_pips": 20}), ea_name="RiskEA")
    # Helper emitted exactly once (function definition appears a single time).
    assert code.count("double CalcLots(double riskPct, double slDistance)") == 1
    # The helper sits file-scope, between CTrade and OnInit.
    assert "CTrade trade;\n" in code
    assert code.index("double CalcLots(") < code.index("int OnInit()")
    # Buy uses computed lots with the risk percent from the RiskManagement node.
    assert "trade.Buy(CalcLots(2.0, slDist_3)" in code
    # Literal lots NOT used for the gated Buy.
    assert 'trade.Buy(0.1' not in code
    # Pip factor + pips-derived SL distance present.
    assert "_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1)" in code
    assert "double slDist_3 = 20 * pip_3;" in code


def test_risk_present_but_sl_pips_zero_stays_literal() -> None:
    """RiskManagement present but sl_pips==0 -> Buy stays literal.

    Note: RiskManagement unconditionally registers the CalcLots helper (per the
    design, so the helper is available regardless of node order), so the helper
    function may be present file-scope — but the Buy itself must NOT call it and
    must keep its literal lots / raw stop_loss.
    """
    code = generate_mql5(
        _risk_graph({"lots": 0.3, "stop_loss": 1.2345}), ea_name="NoSlEA"
    )
    # The Buy keeps literal lots + raw stop_loss and does not size via CalcLots.
    assert 'trade.Buy(0.3, _Symbol, 0.0, 1.2345, 0, "buy");' in code
    assert "trade.Buy(CalcLots" not in code


def test_risk_absent_with_sl_pips_stays_literal() -> None:
    """No RiskManagement node -> even sl_pips>0 yields literal lots, no helper."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Buy", "data": {"lots": 0.2, "sl_pips": 50}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="NoRiskEA")
    assert "CalcLots" not in code
    assert 'trade.Buy(0.2, _Symbol, 0.0, 0, 0, "buy");' in code


def test_risk_path_braces_balanced() -> None:
    """The risk path (with the CalcLots helper) keeps braces balanced."""
    code = generate_mql5(_risk_graph({"sl_pips": 20}), ea_name="BraceEA")
    assert code.count("{") == code.count("}")


def test_sell_risk_path_uses_bid_plus_distance() -> None:
    """A risk-gated Sell derives its stop from Bid + sl_pips distance."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "RiskManagement",
                "data": {"risk_percent": 1.5},
            },
            {"id": "3", "type": "Sell", "data": {"sl_pips": 30}},
            {"id": "4", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
        ],
    }
    code = generate_mql5(graph, ea_name="SellRiskEA")
    assert "double slPrice_3 = bid_3 + slDist_3;" in code
    assert "trade.Sell(CalcLots(1.5, slDist_3)" in code
    assert code.count("double CalcLots(double riskPct, double slDistance)") == 1


# ---------------------------------------------------------------------------
# Phase C: take-profit-by-pips (tp_pips) — mirror of sl_pips, gate decoupled
# ---------------------------------------------------------------------------
def test_tp_pips_default_zero_is_byte_identical() -> None:
    """tp_pips==0 (default) -> raw take_profit flows through, no TP block.

    With no RiskManagement node and default params the Buy stays fully literal:
    the 5th (tp) arg is the raw take_profit (0) and no tp* helper var leaks in.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Buy", "data": {"lots": 0.1, "take_profit": 1.5}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="TpDefEA")
    # Raw take_profit preserved as the 5th positional arg (byte-identical path).
    assert 'trade.Buy(0.1, _Symbol, 0.0, 0, 1.5, "buy");' in code
    # No TP-by-pips machinery emitted.
    assert "tpPrice_" not in code
    assert "tpDist_" not in code
    assert "tpPip_" not in code
    assert "askTp_" not in code


def test_buy_tp_pips_computes_price_from_ask_plus_dist() -> None:
    """Buy tp_pips>0 -> 5th arg is tpPrice = Ask + tp_pips*pip distance."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Buy", "data": {"lots": 0.2, "tp_pips": 40}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="BuyTpEA")
    # Pip factor + pips-derived TP distance present.
    assert "double tpPip_2 = _Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1);" in code
    assert "double tpDist_2 = 40 * tpPip_2;" in code
    assert "double askTp_2 = SymbolInfoDouble(_Symbol, SYMBOL_ASK);" in code
    assert "double tpPrice_2 = askTp_2 + tpDist_2;" in code
    # The computed tpPrice is the 5th positional (tp) arg of the literal Buy.
    assert 'trade.Buy(0.2, _Symbol, 0.0, 0, tpPrice_2, "buy");' in code


def test_sell_tp_pips_computes_price_from_bid_minus_dist() -> None:
    """Sell tp_pips>0 -> 5th arg is tpPrice = Bid - tp_pips*pip distance."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Sell", "data": {"lots": 0.3, "tp_pips": 25}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="SellTpEA")
    assert "double tpDist_2 = 25 * tpPip_2;" in code
    assert "double bidTp_2 = SymbolInfoDouble(_Symbol, SYMBOL_BID);" in code
    assert "double tpPrice_2 = bidTp_2 - tpDist_2;" in code
    assert 'trade.Sell(0.3, _Symbol, 0.0, 0, tpPrice_2, "sell");' in code


def test_tp_pips_without_risk_node_still_emits_block() -> None:
    """tp_pips>0 with NO RiskManagement node still emits the TP block.

    The TP gate is decoupled from RiskManagement (unlike sl_pips). No CalcLots
    helper appears, lots stay literal, but the tp price machinery is present.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Buy", "data": {"lots": 0.1, "tp_pips": 15}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="TpNoRiskEA")
    assert "CalcLots" not in code
    assert "double tpPrice_2 = askTp_2 + tpDist_2;" in code
    assert 'trade.Buy(0.1, _Symbol, 0.0, 0, tpPrice_2, "buy");' in code


def test_sl_pips_and_tp_pips_both_fire_distinct_vars() -> None:
    """sl_pips + tp_pips both>0 (risk path) -> both blocks, distinct vars.

    The 4th (sl) arg becomes slPrice_<id> via the risk path, the 5th (tp) arg
    independently becomes tpPrice_<id>. Var families never collide so there is
    no MQL5 redeclaration, and braces stay balanced.
    """
    code = generate_mql5(
        _risk_graph({"sl_pips": 20, "tp_pips": 30}), ea_name="BothEA"
    )
    # SL block (risk path) present with its own var family.
    assert "double pip_3 = _Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1);" in code
    assert "double slDist_3 = 20 * pip_3;" in code
    assert "double ask_3 = SymbolInfoDouble(_Symbol, SYMBOL_ASK);" in code
    assert "double slPrice_3 = ask_3 - slDist_3;" in code
    # TP block present with its own DISTINCT var family.
    assert "double tpPip_3 = _Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1);" in code
    assert "double tpDist_3 = 30 * tpPip_3;" in code
    assert "double askTp_3 = SymbolInfoDouble(_Symbol, SYMBOL_ASK);" in code
    assert "double tpPrice_3 = askTp_3 + tpDist_3;" in code
    # Distinct var families -> no MQL5 redeclaration of the same name.
    assert code.count("double pip_3 ") == 1
    assert code.count("double tpPip_3 ") == 1
    # The exact both-firing trade line: 4th arg slPrice, 5th arg tpPrice.
    assert (
        "trade.Buy(CalcLots(2.0, slDist_3), "
        '_Symbol, 0.0, slPrice_3, tpPrice_3, "buy");'
    ) in code
    # Flat concatenation keeps braces balanced.
    assert code.count("{") == code.count("}")


# ---------------------------------------------------------------------------
# Phase D: trailing stops (ManageTrailing helper + OnTick-prologue call)
# ---------------------------------------------------------------------------
_TRAIL_CALL = (
    "   ManageTrailing(30 * (_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1)), "
    "10 * (_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1)), "
    "5 * (_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1)));"
)
_TRAIL_DATA = {"trail_pips": 30, "trail_start_pips": 10, "trail_step_pips": 5}


def _trail_graph(node_type: str, extra: dict | None = None) -> dict:
    """A Start -> <Buy|Sell> -> End graph with trailing params on the trade node."""
    data = {"lots": 0.1, **_TRAIL_DATA, **(extra or {})}
    return {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": node_type, "data": data},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }


def test_trail_default_zero_is_byte_identical() -> None:
    """No trail params -> NO ManageTrailing helper, NO prologue, lines unchanged."""
    code = generate_mql5(_graph(), ea_name="NoTrailEA")
    assert "ManageTrailing" not in code
    # OnTick body still opens directly with the first node's comment header (no
    # prologue spliced between the brace and the body).
    assert "void OnTick()\n{\n   // [Start]" in code
    # Existing trade line is untouched.
    assert 'trade.Buy(0.5, _Symbol, 0.0, 0, 0, "buy");' in code


def test_buy_trail_pips_emits_helper_once_and_call() -> None:
    """Buy trail_pips>0 -> ManageTrailing helper defined once + call present."""
    code = generate_mql5(_trail_graph("Buy"), ea_name="BuyTrailEA")
    assert (
        code.count("void ManageTrailing(double trailDist, double startDist, double stepDist)")
        == 1
    )
    assert _TRAIL_CALL in code
    # Helper sits file-scope, before OnInit.
    assert code.index("void ManageTrailing(") < code.index("int OnInit()")


def test_sell_trail_pips_emits_same_call() -> None:
    """Sell trail_pips>0 -> the character-identical ManageTrailing call is emitted."""
    code = generate_mql5(_trail_graph("Sell"), ea_name="SellTrailEA")
    assert _TRAIL_CALL in code
    assert (
        code.count("void ManageTrailing(double trailDist, double startDist, double stepDist)")
        == 1
    )


def test_trail_call_runs_before_entry_guards() -> None:
    """The prologue ManageTrailing call precedes the Start landmark AND any guard.

    Load-bearing placement: trailing is position management and must run every
    tick BEFORE the strategy body and before any Condition early-return guard.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "Condition",
                "data": {"left": "1", "operator": ">", "right": "0"},
            },
            {"id": "3", "type": "Buy", "data": {"lots": 0.1, **_TRAIL_DATA}},
            {"id": "4", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
        ],
    }
    code = generate_mql5(graph, ea_name="TrailGuardEA")
    call_idx = code.index("ManageTrailing(30 *")
    assert call_idx < code.index("Strategy logic begins")
    assert call_idx < code.index("if (!(")


def test_multiple_trailing_nodes_dedup_helper_and_call() -> None:
    """Two trailing nodes (Buy + Sell, identical params) -> one helper, one call."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Buy", "data": {"lots": 0.1, **_TRAIL_DATA}},
            {"id": "3", "type": "Sell", "data": {"lots": 0.1, **_TRAIL_DATA}},
            {"id": "4", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
        ],
    }
    code = generate_mql5(graph, ea_name="MultiTrailEA")
    assert (
        code.count("void ManageTrailing(double trailDist, double startDist, double stepDist)")
        == 1
    )
    assert code.count("ManageTrailing(30 *") == 1


def test_trail_with_tp_sl_and_risk_is_balanced_and_unique() -> None:
    """Trailing + tp_pips + sl_pips + RiskManagement: braces balanced, no dupes."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "RiskManagement",
                "data": {"risk_percent": 2.0},
            },
            {
                "id": "3",
                "type": "Buy",
                "data": {"sl_pips": 20, "tp_pips": 30, **_TRAIL_DATA},
            },
            {"id": "4", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
        ],
    }
    code = generate_mql5(graph, ea_name="ComboEA")
    # Balanced braces despite three helpers + prologue + risk body.
    assert code.count("{") == code.count("}")
    # Each helper defined exactly once (no MQL5 redeclaration).
    assert (
        code.count("void ManageTrailing(double trailDist, double startDist, double stepDist)")
        == 1
    )
    assert code.count("double CalcLots(double riskPct, double slDistance)") == 1
    # The trailing call appears exactly once.
    assert code.count("ManageTrailing(30 *") == 1


# ---------------------------------------------------------------------------
# Phase E: Stochastic oscillator (iStochastic + %K/%D buffer reads)
# ---------------------------------------------------------------------------
def test_stochastic_default_call_and_both_buffer_reads() -> None:
    """A Start -> Stochastic -> End graph emits iStochastic + both buffer reads.

    The iStochastic call carries the default args in the locked order
    (k_period, d_period, slowing, ma_method, price_field) and both output lines
    are copied: MAIN_LINE -> stochk_<id> (%K) and SIGNAL_LINE -> stochd_<id> (%D).
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "Stochastic", "data": {}},
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="StochEA")
    # iStochastic call with the default args in the right order.
    assert "iStochastic(_Symbol, _Period, 14, 3, 3, MODE_SMA, STO_LOWHIGH)" in code
    # Both buffers read: MAIN_LINE -> %K (stochk_), SIGNAL_LINE -> %D (stochd_).
    assert "CopyBuffer(h_stoch_2, MAIN_LINE, 0, 1, stochk_2)" in code
    assert "CopyBuffer(h_stoch_2, SIGNAL_LINE, 0, 1, stochd_2)" in code


def test_stochastic_non_default_params_land_in_correct_slots() -> None:
    """Non-default Stochastic params appear in the right iStochastic slots."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {
                "id": "2",
                "type": "Stochastic",
                "data": {
                    "k_period": 21,
                    "d_period": 5,
                    "slowing": 7,
                    "ma_method": "MODE_EMA",
                    "price_field": "STO_CLOSECLOSE",
                },
            },
            {"id": "3", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
        ],
    }
    code = generate_mql5(graph, ea_name="StochNonDefEA")
    assert "iStochastic(_Symbol, _Period, 21, 5, 7, MODE_EMA, STO_CLOSECLOSE)" in code


def test_stochastic_operand_ref_parity_uses_same_buffer_vars() -> None:
    """Operand-ref parity contract: FE operand prefix == BE buffer var name.

    A Condition node referencing ``stochk_<id>[0]`` / ``stochd_<id>[0]`` must
    generate MQL5 against the SAME buffer variables emitted by the Stochastic
    codegen module (stochk_<id> / stochd_<id>). This guards the cross-language
    operand-ref parity between frontend conditionOperand prefixes and backend
    buffer declarations.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "X", "type": "Stochastic", "data": {}},
            {
                "id": "3",
                "type": "Condition",
                "data": {
                    "left": "stochk_X[0]",
                    "operator": ">",
                    "right": "stochd_X[0]",
                },
            },
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "X"},
            {"source": "X", "target": "3"},
            {"source": "3", "target": "5"},
        ],
    }
    code = generate_mql5(graph, ea_name="StochOperandEA")
    # Buffers declared/read by the Stochastic module.
    assert "double stochk_X[];" in code
    assert "double stochd_X[];" in code
    assert "CopyBuffer(h_stoch_X, MAIN_LINE, 0, 1, stochk_X)" in code
    assert "CopyBuffer(h_stoch_X, SIGNAL_LINE, 0, 1, stochd_X)" in code
    # The Condition guard references the SAME buffer variables (no rename drift).
    assert "if (!(stochk_X[0] > stochd_X[0])) return;" in code


# ---------------------------------------------------------------------------
# Phase F: boolean combinators (LogicalAnd / LogicalOr / LogicalNot)
# ---------------------------------------------------------------------------
def _cond(node_id: str, left: str, op: str, right: str) -> dict:
    """A Condition node in the real editor shape."""
    return {
        "id": node_id,
        "type": "custom",
        "data": {"type": "Condition", "left": left, "operator": op, "right": right},
    }


def _cond_edge(src: str, tgt: str, slot: int) -> dict:
    """A real CONDITION edge: Condition ``src`` -> combinator ``tgt`` on condN."""
    return {"source": src, "target": tgt, "targetHandle": f"cond{slot}"}


def test_logical_and_all_mode_emits_and_guard() -> None:
    """requerir_todas=True with 2 conditions -> single (e1 && e2) guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {
                "id": "and",
                "type": "custom",
                "data": {"type": "LogicalAnd", "requerir_todas": True},
            },
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        # Real handle shape: Conditions feed the combinator via cond1/cond2
        # (CONDITION edges, NOT the flow chain). The combinator is reached only
        # by its OUTPUT flow edge (and -> End), exercising the unreachable-guard
        # placement path.
        "edges": [
            {"source": "1", "target": "9"},
            _cond_edge("c1", "and", 1),
            _cond_edge("c2", "and", 2),
            {"source": "and", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="AndAllEA")
    assert "if (!((a > 1) && (b < 2))) return;" in code
    # The fed Conditions did NOT emit their own standalone guard.
    assert "if (!(a > 1)) return;" not in code
    assert "if (!(b < 2)) return;" not in code
    assert "[unknown node type" not in code


def test_logical_and_count_mode_emits_threshold_guard() -> None:
    """requerir_todas=False, min_true=2, 3 conditions -> count >= 2 guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            _cond("c3", "c", ">", "3"),
            {
                "id": "and",
                "type": "custom",
                "data": {
                    "type": "LogicalAnd",
                    "requerir_todas": False,
                    "min_true": 2,
                },
            },
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "9"},
            _cond_edge("c1", "and", 1),
            _cond_edge("c2", "and", 2),
            _cond_edge("c3", "and", 3),
            {"source": "and", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="AndCountEA")
    assert (
        "if (!((((a > 1)?1:0)+((b < 2)?1:0)+((c > 3)?1:0)) >= 2)) return;" in code
    )


def test_logical_or_emits_or_guard() -> None:
    """LogicalOr (no params) -> (e1 || e2) guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {"id": "or", "type": "custom", "data": {"type": "LogicalOr"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "9"},
            _cond_edge("c1", "or", 1),
            _cond_edge("c2", "or", 2),
            {"source": "or", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="OrEA")
    assert "if (!((a > 1) || (b < 2))) return;" in code


def test_logical_not_emits_negated_guard() -> None:
    """LogicalNot with one input -> !(e1) inside the guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c1", "a", ">", "1"),
            {"id": "not", "type": "custom", "data": {"type": "LogicalNot"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "9"},
            _cond_edge("c1", "not", 1),
            {"source": "not", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="NotEA")
    assert "if (!(!(a > 1))) return;" in code


def test_combinator_off_chain_condition_does_not_double_emit() -> None:
    """A Condition feeding a combinator is off-chain; a standalone one still emits."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            # Standalone on-chain Condition (emits its own guard).
            _cond("c0", "x", ">", "0"),
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {"id": "or", "type": "custom", "data": {"type": "LogicalOr"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        # c0 is on the flow chain (emits its own guard); c1/c2 feed the
        # combinator via CONDITION handles (off-chain, inlined by the combinator).
        "edges": [
            {"source": "1", "target": "c0"},
            {"source": "c0", "target": "9"},
            _cond_edge("c1", "or", 1),
            _cond_edge("c2", "or", 2),
            {"source": "or", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="OffChainEA")
    # Standalone Condition c0 still emits its own guard.
    assert "if (!(x > 0)) return;" in code
    # The wired-into-combinator Conditions do NOT emit standalone guards.
    assert "if (!(a > 1)) return;" not in code
    assert "if (!(b < 2)) return;" not in code
    # The combinator emits the combined guard exactly once.
    assert code.count("if (!((a > 1) || (b < 2))) return;") == 1


def test_combinator_zero_inputs_is_noop_no_guard() -> None:
    """A combinator with no connected Condition emits a no-op comment, no guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            {"id": "and", "type": "custom", "data": {"type": "LogicalAnd"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        # Unwired combinator: no CONDITION inputs, reached only by its output edge.
        "edges": [
            {"source": "1", "target": "9"},
            {"source": "and", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="NoopEA")
    assert "no inputs connected" in code
    # No early-return guard was emitted by the unwired combinator.
    assert "return;" not in code.split("OnTick")[1]


def test_logical_xor_emits_exactly_one_true_guard() -> None:
    """LogicalXor (no params) with 2 conditions -> (count == 1) guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {"id": "xor", "type": "custom", "data": {"type": "LogicalXor"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "9"},
            _cond_edge("c1", "xor", 1),
            _cond_edge("c2", "xor", 2),
            {"source": "xor", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="XorEA")
    # condition_expr already parenthesizes each term; (e?1:0) then mirrors the
    # LogicalAnd count-mode form. XOR requires the running sum to equal one.
    assert (
        "if (!((((a > 1)?1:0) + ((b < 2)?1:0)) == 1)) return;" in code
    )
    # The fed Conditions did NOT emit their own standalone guard.
    assert "if (!(a > 1)) return;" not in code
    assert "if (!(b < 2)) return;" not in code
    assert "[unknown node type" not in code


def test_logical_xor_off_chain_condition_does_not_double_emit() -> None:
    """Conditions wired into a LogicalXor are off-chain; a standalone one emits."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            _cond("c0", "x", ">", "0"),
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {"id": "xor", "type": "custom", "data": {"type": "LogicalXor"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "c0"},
            {"source": "c0", "target": "9"},
            _cond_edge("c1", "xor", 1),
            _cond_edge("c2", "xor", 2),
            {"source": "xor", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="XorOffChainEA")
    # Standalone Condition c0 still emits its own guard.
    assert "if (!(x > 0)) return;" in code
    # The wired-into-combinator Conditions do NOT emit standalone guards.
    assert "if (!(a > 1)) return;" not in code
    assert "if (!(b < 2)) return;" not in code
    # The combinator emits the combined count==1 guard exactly once.
    assert (
        code.count("if (!((((a > 1)?1:0) + ((b < 2)?1:0)) == 1)) return;") == 1
    )


def test_logical_xor_zero_inputs_is_noop_no_guard() -> None:
    """A LogicalXor with no connected Condition emits a no-op comment, no guard."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            {"id": "xor", "type": "custom", "data": {"type": "LogicalXor"}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
        ],
        "edges": [
            {"source": "1", "target": "9"},
            {"source": "xor", "target": "9"},
        ],
    }
    code = generate_mql5(graph, ea_name="XorNoopEA")
    assert "no inputs connected" in code
    # No early-return guard was emitted by the unwired combinator.
    assert "return;" not in code.split("OnTick")[1]


# ---------------------------------------------------------------------------
# Phase G: crossing nodes (BullishCross / BearishCross)
# ---------------------------------------------------------------------------
def _cross_graph(cross_type: str, cross_data: dict, *, connect_b: bool = True) -> dict:
    """Start -> SMA(A) -> End with the crossing fed ONLY by VALUE edges.

    SMA A is on the flow chain AND wired to value1; SMA B is value2-only
    (off-chain). The crossing X has NO flow-in edge — it is reached purely by its
    OUTPUT flow edge (X -> End), exercising the unreachable-guard placement path.
    The crossing reads value1/value2 via the handle-aware edges.
    """
    edges = [
        {"source": "1", "target": "A"},
        {"source": "A", "target": "9"},
        {"source": "X", "target": "9"},
        {"source": "A", "target": "X", "targetHandle": "value1"},
    ]
    if connect_b:
        edges.append({"source": "B", "target": "X", "targetHandle": "value2"})
    return {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "A", "type": "SMA", "data": {"period": 10}},
            {"id": "B", "type": "SMA", "data": {"period": 20}},
            {"id": "X", "type": cross_type, "data": cross_data},
            {"id": "9", "type": "End", "data": {}},
        ],
        "edges": edges,
    }


def test_bullish_cross_defaults_emits_prev_now_guard() -> None:
    """Bullish defaults (s=1): before [2], now [1], V1 over V2."""
    code = generate_mql5(_cross_graph("BullishCross", {}), ea_name="BullEA")
    assert "if (!(sma_A[2] < sma_B[2] && sma_A[1] > sma_B[1])) return;" in code


def test_bearish_cross_defaults_flips_operators() -> None:
    """Bearish defaults flip both comparisons (V1 under V2)."""
    code = generate_mql5(_cross_graph("BearishCross", {}), ea_name="BearEA")
    assert "if (!(sma_A[2] > sma_B[2] && sma_A[1] < sma_B[1])) return;" in code


def test_cross_barras_confirmacion_shifts_indices_and_depth() -> None:
    """barras_confirmacion=2 -> indices [3] vs [2]; consumed SMA copies depth 4."""
    code = generate_mql5(
        _cross_graph("BullishCross", {"barras_confirmacion": 2}), ea_name="ConfEA"
    )
    assert "if (!(sma_A[3] < sma_B[3] && sma_A[2] > sma_B[2])) return;" in code
    # depth = s + 2 = 4 for both consumed indicators.
    assert "CopyBuffer(h_sma_A, 0, 0, 4, sma_A)" in code
    assert "CopyBuffer(h_sma_B, 0, 0, 4, sma_B)" in code


def test_cross_pips_positive_appends_mathabs_term() -> None:
    """distancia_minima_pips>0 appends the MathAbs pip-distance term."""
    code = generate_mql5(
        _cross_graph("BullishCross", {"distancia_minima_pips": 5}), ea_name="PipEA"
    )
    assert (
        "if (!(sma_A[2] < sma_B[2] && sma_A[1] > sma_B[1] && "
        "MathAbs(sma_A[1] - sma_B[1]) >= "
        "5*_Point * ((_Digits == 3 || _Digits == 5) ? 10 : 1))) return;"
    ) in code


def test_cross_pips_zero_omits_mathabs_term() -> None:
    """distancia_minima_pips=0 (default) emits NO MathAbs term."""
    code = generate_mql5(
        _cross_graph("BullishCross", {"distancia_minima_pips": 0}), ea_name="NoPipEA"
    )
    assert "if (!(sma_A[2] < sma_B[2] && sma_A[1] > sma_B[1])) return;" in code
    assert "MathAbs" not in code


def test_cross_filtrar_ruido_false_uses_le_on_before() -> None:
    """filtrar_ruido=False relaxes the BEFORE comparison to <= (bullish)."""
    code = generate_mql5(
        _cross_graph("BullishCross", {"filtrar_ruido": False}), ea_name="RuidoEA"
    )
    assert "if (!(sma_A[2] <= sma_B[2] && sma_A[1] > sma_B[1])) return;" in code


def test_cross_usar_metodo_desplazamiento_is_ignored_in_v1() -> None:
    """usar_metodo_desplazamiento True and False produce identical output (v1)."""
    on = generate_mql5(
        _cross_graph("BullishCross", {"usar_metodo_desplazamiento": True}),
        ea_name="DespEA",
    )
    off = generate_mql5(
        _cross_graph("BullishCross", {"usar_metodo_desplazamiento": False}),
        ea_name="DespEA",
    )
    assert on == off


def test_cross_missing_value_input_is_noop_no_guard() -> None:
    """An unconnected value2 input -> no-op comment, NO guard."""
    code = generate_mql5(
        _cross_graph("BullishCross", {}, connect_b=False), ea_name="MissEA"
    )
    assert "missing value input" in code
    assert "if (!(sma_A" not in code


def test_cross_copy_depth_bump_only_on_consumed_indicators() -> None:
    """An indicator consumed by a crossing copies depth 3 (s=1 default)."""
    code = generate_mql5(_cross_graph("BullishCross", {}), ea_name="DepthEA")
    assert "CopyBuffer(h_sma_A, 0, 0, 3, sma_A)" in code
    assert "CopyBuffer(h_sma_B, 0, 0, 3, sma_B)" in code
    # The deeper copies mark the destination arrays as series-indexed.
    assert "ArraySetAsSeries(sma_A, true);" in code
    assert "ArraySetAsSeries(sma_B, true);" in code


def test_depth_one_byte_identity_for_indicator_only_graph() -> None:
    """A crossing-free indicator graph generates the EXACT legacy code.

    Guards the additive-depth refactor: with no crossing, copy depth stays 1 and
    the CopyBuffer lines (and the absence of ArraySetAsSeries) are byte-identical.
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "2", "type": "SMA", "data": {"period": 50}},
            {"id": "3", "type": "RSI", "data": {"period": 14}},
            {"id": "4", "type": "MACD", "data": {}},
            {"id": "5", "type": "End", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "2"},
            {"source": "2", "target": "3"},
            {"source": "3", "target": "4"},
            {"source": "4", "target": "5"},
        ],
    }
    code = generate_mql5(graph, ea_name="ByteEA")
    # Exact legacy single-bar reads, no series setup spliced in.
    assert "   CopyBuffer(h_sma_2, 0, 0, 1, sma_2);" in code
    assert "   CopyBuffer(h_rsi_3, 0, 0, 1, rsi_3);" in code
    assert "   CopyBuffer(h_macd_4, MAIN_LINE, 0, 1, macd_4);" in code
    assert "ArraySetAsSeries" not in code


# ---------------------------------------------------------------------------
# Phase H: guard placement by OUTPUT flow edge (insertion-order independence)
#
# GUARD nodes (combinators + crossings) have NO flow-IN handle, so the Start DFS
# never reaches them and they fall to the unreachable-append path. Previously
# they were appended in node-INSERTION order, which could emit their
# ``if (!...) return;`` AFTER the Buy/Sell they gate. These tests build graphs
# where the guard appears AFTER the Buy in nodes[] and assert the guard's
# early-return precedes ``trade.Buy(...)``.
# ---------------------------------------------------------------------------
def _assert_guard_before_buy(code: str) -> None:
    """Assert a ``if (!(...)) return;`` guard precedes ``trade.Buy(`` in OnTick."""
    body = code.split("void OnTick()")[1]
    guard_at = body.find("return;")
    buy_at = body.find("trade.Buy(")
    assert guard_at != -1, f"no guard emitted:\n{body}"
    assert buy_at != -1, f"no trade.Buy emitted:\n{body}"
    assert guard_at < buy_at, f"guard must precede trade.Buy:\n{body}"


def test_logical_and_guard_after_buy_in_nodes_still_emits_before_buy() -> None:
    """Adversarial: LogicalAnd listed AFTER Buy gates the Buy (guard emits first)."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            # Buy appears BEFORE the guard in insertion order.
            {"id": "buy", "type": "custom", "data": {"type": "Buy", "lots": 0.3}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {
                "id": "and",
                "type": "custom",
                "data": {"type": "LogicalAnd", "requerir_todas": True},
            },
        ],
        # Flow chain Start -> Buy -> End; the combinator's OUTPUT flow edge
        # (and -> buy) is what positions its guard immediately before the Buy.
        "edges": [
            {"source": "1", "target": "buy"},
            {"source": "buy", "target": "9"},
            _cond_edge("c1", "and", 1),
            _cond_edge("c2", "and", 2),
            {"source": "and", "target": "buy"},
        ],
    }
    code = generate_mql5(graph, ea_name="AdvAndEA")
    assert "if (!((a > 1) && (b < 2))) return;" in code
    assert "trade.Buy(0.3" in code
    _assert_guard_before_buy(code)


def test_bullish_cross_guard_after_buy_in_nodes_still_emits_before_buy() -> None:
    """Adversarial: BullishCross listed AFTER Buy gates the Buy (guard first)."""
    graph = {
        "nodes": [
            {"id": "1", "type": "Start", "data": {}},
            {"id": "A", "type": "SMA", "data": {"period": 10}},
            {"id": "B", "type": "SMA", "data": {"period": 20}},
            {"id": "buy", "type": "Buy", "data": {"lots": 0.2}},
            {"id": "9", "type": "End", "data": {}},
            # The crossing node appears LAST in insertion order.
            {"id": "X", "type": "BullishCross", "data": {}},
        ],
        "edges": [
            {"source": "1", "target": "A"},
            {"source": "A", "target": "buy"},
            {"source": "buy", "target": "9"},
            {"source": "A", "target": "X", "targetHandle": "value1"},
            {"source": "B", "target": "X", "targetHandle": "value2"},
            {"source": "X", "target": "buy"},
        ],
    }
    code = generate_mql5(graph, ea_name="AdvCrossEA")
    assert "if (!(sma_A[2] < sma_B[2] && sma_A[1] > sma_B[1])) return;" in code
    assert "trade.Buy(0.2" in code
    _assert_guard_before_buy(code)
    # The value-source indicators were hoisted before the crossing guard so the
    # buffer vars are declared before the guard references them.
    body = code.split("void OnTick()")[1]
    assert body.find("CopyBuffer(h_sma_A") < body.find(
        "sma_A[2] < sma_B[2]"
    )
    assert body.find("CopyBuffer(h_sma_B") < body.find(
        "sma_A[2] < sma_B[2]"
    )


def test_two_guards_in_series_order_deepest_first_before_buy() -> None:
    """A guard whose output targets another guard: both precede the Buy in order.

    ``and1 -> and2 -> buy`` (output flow edges). ``and2`` gates the Buy and must
    sit immediately before it; ``and1`` gates ``and2`` and must sit before
    ``and2``. Both guards are listed AFTER the Buy in nodes[].
    """
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            {"id": "buy", "type": "custom", "data": {"type": "Buy", "lots": 0.1}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
            _cond("c1", "a", ">", "1"),
            _cond("c2", "b", "<", "2"),
            {
                "id": "and2",
                "type": "custom",
                "data": {"type": "LogicalAnd", "requerir_todas": True},
            },
            {"id": "and1", "type": "custom", "data": {"type": "LogicalOr"}},
        ],
        "edges": [
            {"source": "1", "target": "buy"},
            {"source": "buy", "target": "9"},
            _cond_edge("c1", "and1", 1),
            _cond_edge("c2", "and2", 1),
            # Guard chain: and1 gates and2, and2 gates the Buy.
            {"source": "and1", "target": "and2"},
            {"source": "and2", "target": "buy"},
        ],
    }
    code = generate_mql5(graph, ea_name="SeriesGuardEA")
    body = code.split("void OnTick()")[1]
    # and1 is a LogicalOr fed by a single condition -> (a > 1); and2 a LogicalAnd
    # fed by a single condition -> (b < 2).
    and1_at = body.find("if (!((a > 1))) return;")
    and2_at = body.find("if (!((b < 2))) return;")
    buy_at = body.find("trade.Buy(")
    assert and1_at != -1 and and2_at != -1 and buy_at != -1, body
    # Deepest guard (and1) first, then and2, then the Buy.
    assert and1_at < and2_at < buy_at, body


def test_guard_with_no_output_edge_emits_harmlessly() -> None:
    """A guard with NO output flow edge is appended at the tail (no crash)."""
    graph = {
        "nodes": [
            {"id": "1", "type": "custom", "data": {"type": "Start"}},
            {"id": "buy", "type": "custom", "data": {"type": "Buy", "lots": 0.1}},
            {"id": "9", "type": "custom", "data": {"type": "End"}},
            _cond("c1", "a", ">", "1"),
            # Dangling guard: fed by a Condition but with NO output flow edge.
            {
                "id": "and",
                "type": "custom",
                "data": {"type": "LogicalAnd", "requerir_todas": True},
            },
        ],
        "edges": [
            {"source": "1", "target": "buy"},
            {"source": "buy", "target": "9"},
            _cond_edge("c1", "and", 1),
        ],
    }
    code = generate_mql5(graph, ea_name="DanglingGuardEA")
    # It still emits its guard (tail-appended) and the graph compiles cleanly.
    assert "if (!((a > 1))) return;" in code
    assert "trade.Buy(0.1" in code
    assert code.count("{") == code.count("}")
    assert "[unknown node type" not in code


