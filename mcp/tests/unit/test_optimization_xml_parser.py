"""Unit tests for the MT5 optimization XML cache parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_metatrader5.parsers.optimization_xml import (
    OptimizationPass,
    OptimizationReport,
    parse_optimization_xml,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parses_three_passes() -> None:
    report = parse_optimization_xml(_FIXTURES / "optimization_report.xml")
    assert isinstance(report, OptimizationReport)
    assert report.pass_count == 3
    assert all(isinstance(p, OptimizationPass) for p in report.passes)


def test_parses_first_pass_metrics() -> None:
    report = parse_optimization_xml(_FIXTURES / "optimization_report.xml")
    first = report.passes[0]
    assert first.pass_index == 1
    assert first.profit == pytest.approx(1234.56)
    assert first.profit_factor == pytest.approx(1.33)
    assert first.expected_payoff == pytest.approx(5.20)
    assert first.recovery_factor == pytest.approx(2.10)
    assert first.sharpe_ratio == pytest.approx(0.85)
    assert first.equity_drawdown_percent == pytest.approx(8.0)
    assert first.trades == 237


def test_extracts_parameter_values_per_pass() -> None:
    report = parse_optimization_xml(_FIXTURES / "optimization_report.xml")
    assert report.passes[0].parameters == {"Lots": "0.10", "StopLoss": "200"}
    assert report.passes[1].parameters == {"Lots": "0.15", "StopLoss": "200"}
    assert report.passes[2].parameters == {"Lots": "0.20", "StopLoss": "300"}


def test_columns_recorded() -> None:
    report = parse_optimization_xml(_FIXTURES / "optimization_report.xml")
    assert "Profit" in report.columns
    assert "Lots" in report.columns
    assert "StopLoss" in report.columns


def test_best_pass_helper() -> None:
    report = parse_optimization_xml(_FIXTURES / "optimization_report.xml")
    best = report.best_pass(by="profit")
    assert best is not None
    assert best.pass_index == 1


def test_missing_file_raises(tmp_path: Path) -> None:
    from mcp_metatrader5.errors import MT5MCPError

    with pytest.raises(MT5MCPError):
        parse_optimization_xml(tmp_path / "nope.xml")
