"""Unit coverage for the risk classifier — every bracket + the special rules."""

from __future__ import annotations

from decimal import Decimal

from aether_api.sleep.classifier import classify_changes


def _base() -> dict:
    return {
        "risk_per_trade": Decimal("1.0"),
        "max_daily_dd": Decimal("3.0"),
        "max_total_dd": Decimal("8.0"),
        "max_exposure": Decimal("10.0"),
        "trading_sessions": ["europe", "new_york"],
        "worker_params": {"sma_window": 30},
        "investigator_params": {},
        "auditor_params": {},
        "notes": "baseline",
    }


def test_no_changes_is_bajo() -> None:
    snapshot = _base()
    assert classify_changes(current=snapshot, proposed=dict(snapshot)) == "bajo"


def test_risk_cap_touch_is_alto() -> None:
    cur = _base()
    prop = dict(cur)
    prop["risk_per_trade"] = Decimal("1.05")  # only +5% but it's a risk cap
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_trading_sessions_touch_is_alto() -> None:
    cur = _base()
    prop = dict(cur)
    prop["trading_sessions"] = ["europe"]  # removed new_york
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_numeric_low_bracket_is_bajo() -> None:
    cur = _base()
    prop = dict(cur)
    prop["worker_params"] = {"sma_window": 32}  # +2/30 ≈ 6.7%
    assert classify_changes(current=cur, proposed=prop) == "bajo"


def test_numeric_mid_bracket_is_medio() -> None:
    cur = _base()
    prop = dict(cur)
    prop["worker_params"] = {"sma_window": 36}  # +6/30 = 20%
    assert classify_changes(current=cur, proposed=prop) == "medio"


def test_numeric_above_30pct_is_alto() -> None:
    cur = _base()
    prop = dict(cur)
    prop["worker_params"] = {"sma_window": 60}  # +30/30 = 100%
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_unknown_field_is_alto() -> None:
    cur = _base()
    prop = dict(cur)
    prop["totally_new_field"] = "anything"
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_zero_to_nonzero_is_alto() -> None:
    cur = _base()
    cur["worker_params"] = {"slippage_pips": 0}
    prop = dict(cur)
    prop["worker_params"] = {"slippage_pips": 1}
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_worst_class_wins_across_keys() -> None:
    cur = _base()
    prop = dict(cur)
    # One bajo + one alto → alto.
    prop["worker_params"] = {"sma_window": 31}
    prop["risk_per_trade"] = Decimal("1.05")
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_inner_risk_cap_in_params_is_alto() -> None:
    cur = _base()
    cur["worker_params"] = {"risk_per_trade": Decimal("1.0")}
    prop = dict(cur)
    prop["worker_params"] = {"risk_per_trade": Decimal("1.01")}  # +1% but risk-cap-named
    assert classify_changes(current=cur, proposed=prop) == "alto"


def test_logica_field_change_is_alto() -> None:
    cur = _base()
    cur["logica"] = "def on_tick(ctx):\n    return None\n"
    prop = dict(cur)
    prop["logica"] = "def on_tick(ctx):\n    return {'a': 1}\n"
    assert classify_changes(current=cur, proposed=prop) == "alto"
