"""Unit tests for the MT5 backtest HTML report parser."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mcp_metatrader5.parsers.html_report import parse_backtest_report

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parses_settings() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    assert report.symbol == "XAUUSD"
    assert report.period == "D1"
    assert report.from_date == "2024.01.01"
    assert report.to_date == "2024.06.30"
    assert report.initial_deposit == pytest.approx(10000.0)
    assert report.leverage == "1:100"
    assert report.expert == "managed/my-ea/my-ea.ex5"


def test_parses_inputs() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    assert report.inputs == {"Lots": "0.10", "StopLoss": "200"}


def test_parses_summary_metrics() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    assert report.total_net_profit == pytest.approx(1234.56)
    assert report.gross_profit == pytest.approx(5000.0)
    assert report.gross_loss == pytest.approx(-3765.44)
    assert report.profit_factor == pytest.approx(1.33)
    assert report.expected_payoff == pytest.approx(5.20)
    assert report.recovery_factor == pytest.approx(2.10)
    assert report.sharpe_ratio == pytest.approx(0.85)


def test_parses_trade_counts() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    assert report.total_trades == 237
    assert report.profit_trades == 138
    assert report.loss_trades == 99


def test_parses_drawdown() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    assert report.max_drawdown_money == pytest.approx(800.0)
    assert report.max_drawdown_percent == pytest.approx(8.0)


def test_report_is_immutable() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        report.symbol = "EURUSD"  # type: ignore[misc]


def test_missing_file_raises(tmp_path: Path) -> None:
    from mcp_metatrader5.errors import MT5MCPError

    with pytest.raises(MT5MCPError):
        parse_backtest_report(tmp_path / "nope.html")


def test_to_dict_round_trip() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    d = report.to_dict()
    assert d["symbol"] == "XAUUSD"
    assert d["total_net_profit"] == pytest.approx(1234.56)
    assert d["inputs"] == {"Lots": "0.10", "StopLoss": "200"}
    # round-trip via dataclasses
    assert isinstance(d, dict)


def test_unknown_metric_is_kept_in_extras() -> None:
    report = parse_backtest_report(_FIXTURES / "backtest_report.html")
    # 'Balance Drawdown Absolute' is not first-class — should land in extras.
    assert any("balance" in k.lower() for k in report.extras)
