"""Unit tests for the MT5 terminal INI builder (backtest + optimize)."""

from __future__ import annotations

import dataclasses

import pytest

from mcp_metatrader5.builders.ini import (
    BacktestConfig,
    OptimizationConfig,
    OptimizationCriterion,
    OptimizationMode,
    TickModel,
    build_backtest_ini,
    build_optimize_ini,
)


def _base_backtest() -> BacktestConfig:
    return BacktestConfig(
        expert="managed/my-ea/my-ea.ex5",
        symbol="XAUUSD",
        period="D1",
        from_date="2024.01.01",
        to_date="2024.06.30",
        deposit=10000.0,
        currency="USD",
        leverage=100,
        model=TickModel.EVERY_TICK,
        report="report.html",
    )


def test_backtest_ini_contains_tester_section() -> None:
    text = build_backtest_ini(_base_backtest())
    assert "[Tester]" in text


def test_backtest_ini_required_keys_present() -> None:
    text = build_backtest_ini(_base_backtest())
    expected_kvs = [
        "Expert=managed/my-ea/my-ea.ex5",
        "Symbol=XAUUSD",
        "Period=D1",
        "FromDate=2024.01.01",
        "ToDate=2024.06.30",
        "Deposit=10000",
        "Currency=USD",
        "Leverage=1:100",
        "Model=0",
        "Optimization=0",
        "ShutdownTerminal=1",
        "Report=report.html",
        "ReplaceReport=1",
    ]
    for kv in expected_kvs:
        assert kv in text, f"missing {kv} in:\n{text}"


def test_backtest_ini_has_crlf_line_endings() -> None:
    text = build_backtest_ini(_base_backtest())
    # Windows tools expect CRLF in INI files; ensure we use \r\n separators.
    assert "\r\n" in text
    # No bare LF without CR.
    bare_lfs = sum(1 for i, ch in enumerate(text) if ch == "\n" and (i == 0 or text[i - 1] != "\r"))
    assert bare_lfs == 0


def test_backtest_ini_inputs_are_appended_as_inputs_section() -> None:
    cfg = dataclasses.replace(
        _base_backtest(), inputs={"Lots": "0.10", "StopLoss": "200"}
    )
    text = build_backtest_ini(cfg)
    assert "[TesterInputs]" in text
    assert "Lots=0.10" in text
    assert "StopLoss=200" in text


def test_backtest_ini_rejects_bad_period() -> None:
    bad = dataclasses.replace(_base_backtest(), period="Q1")
    with pytest.raises(ValueError, match="period"):
        build_backtest_ini(bad)


def test_backtest_ini_rejects_bad_date() -> None:
    bad = dataclasses.replace(_base_backtest(), from_date="2024-01-01")
    with pytest.raises(ValueError, match="from_date"):
        build_backtest_ini(bad)


# ---------------- optimize ----------------


def _base_optimize() -> OptimizationConfig:
    return OptimizationConfig(
        expert="managed/my-ea/my-ea.ex5",
        symbol="XAUUSD",
        period="H1",
        from_date="2024.01.01",
        to_date="2024.12.31",
        deposit=10000.0,
        currency="USD",
        leverage=100,
        model=TickModel.OPEN_PRICES,
        optimization_mode=OptimizationMode.SLOW_GENETIC,
        optimization_criterion=OptimizationCriterion.MAX_PROFIT,
        report="opt.xml",
        parameters={
            "Lots": ("0.10", "0.05", "0.50", True),
            "StopLoss": ("100", "50", "500", True),
        },
    )


def test_optimize_ini_sets_optimization_flag() -> None:
    text = build_optimize_ini(_base_optimize())
    assert "Optimization=2" in text  # slow genetic = 2
    assert "OptimizationCriterion=" in text


def test_optimize_ini_includes_parameter_ranges() -> None:
    text = build_optimize_ini(_base_optimize())
    assert "[TesterInputs]" in text
    # MT5 INI ranges are start||min||step||max||enabled
    assert "Lots=0.10||0.05||0.05||0.50||Y" in text or "Lots=0.10||0.10||0.05||0.50||Y" in text
    assert "StopLoss=100||" in text and "||500||Y" in text


def test_optimize_ini_disabled_param_uses_N_flag() -> None:
    bad_params = {"Lots": ("0.10", "0.05", "0.50", False)}
    cfg = dataclasses.replace(_base_optimize(), parameters=bad_params)
    text = build_optimize_ini(cfg)
    assert "Lots=0.10||" in text
    assert "||N" in text
