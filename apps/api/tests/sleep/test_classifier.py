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


# ---------------------------------------------------------------------------
# Q-Table delta integration (sleep-learning-loop, design #2070).
# ---------------------------------------------------------------------------
# The Q-Table classifier in :mod:`aether_api.learning.qtable_versioning`
# is exercised in detail under ``tests/learning/test_qtable_versioning.py``.
# The tests below pin the **composition** with the rest of the sleep
# classifier — max-severity merge of the two branches.


class _StubProject:
    """Stand-in for the Project columns the classifier reads."""

    def __init__(
        self,
        *,
        max_exposure: float = 10.0,
        risk_per_trade: float = 1.0,
        lot_size_default: float | None = None,
    ) -> None:
        self.max_exposure = max_exposure
        self.risk_per_trade = risk_per_trade
        self.lot_size_default = lot_size_default


def test_qtable_delta_is_ignored_when_unsupplied() -> None:
    """Default invocation behaves exactly like the pre-Phase-5 classifier."""
    snap = _base()
    assert classify_changes(current=snap, proposed=dict(snap)) == "bajo"


def test_qtable_delta_alone_can_escalate_to_alto() -> None:
    """Config-side bajo + Q-Table-side alto ⇒ alto (max-severity merge)."""
    snap = _base()
    project = _StubProject(max_exposure=5.0, lot_size_default=20.0)
    qtable_delta = {
        "old": {"s": {"open_long": 0.50}},
        "new": {"s": {"open_long": 0.51}},  # trivial magnitude
    }
    result = classify_changes(
        current=snap,
        proposed=dict(snap),
        qtable_delta=qtable_delta,
        project=project,
        top_k_states=[("s", 100)],
    )
    assert result == "alto"


def test_qtable_delta_medio_floors_to_medio_when_config_is_bajo() -> None:
    """Config-side bajo + Q-Table-side medio ⇒ medio (max-severity)."""
    cur = _base()
    prop = dict(cur)
    prop["worker_params"] = {"sma_window": 31}  # +1/30 ≈ 3.3% → bajo
    project = _StubProject()
    qtable_delta = {
        "old": {"s": {"close": 1.0}},
        "new": {"s": {"close": 1.2}},  # 20% → medio
    }
    result = classify_changes(
        current=cur,
        proposed=prop,
        qtable_delta=qtable_delta,
        project=project,
        top_k_states=[],  # cold start — magnitude path
    )
    assert result == "medio"


def test_config_alto_dominates_qtable_bajo() -> None:
    """Config-side alto already triggers — Q-Table check cannot relax it."""
    cur = _base()
    prop = dict(cur)
    prop["risk_per_trade"] = Decimal("1.05")  # risk-cap touch ⇒ alto
    project = _StubProject()
    qtable_delta = {
        "old": {"s": {"close": 1.0}},
        "new": {"s": {"close": 1.00}},  # 0% → bajo
    }
    result = classify_changes(
        current=cur,
        proposed=prop,
        qtable_delta=qtable_delta,
        project=project,
        top_k_states=[("s", 10)],
    )
    assert result == "alto"


def test_qtable_delta_ignored_when_project_missing() -> None:
    """Defence-in-depth: missing ``project`` ⇒ Q-Table branch skipped.

    The classifier MUST NOT crash and MUST NOT silently substitute a
    default project — leaking that path could mask a real escalation.
    """
    snap = _base()
    qtable_delta = {
        "old": {"s": {"open_long": 0.50}},
        "new": {"s": {"open_long": 0.51}},
    }
    # No ``project`` passed — branch silently skipped, only the config
    # side (bajo because snap == snap) decides the outcome.
    result = classify_changes(
        current=snap,
        proposed=dict(snap),
        qtable_delta=qtable_delta,
        project=None,
        top_k_states=[("s", 1)],
    )
    assert result == "bajo"
