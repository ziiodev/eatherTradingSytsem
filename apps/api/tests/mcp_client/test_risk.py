"""RiskEnforcer — pure rule tests.

Each test exercises ONE rule (SL, risk_per_trade, exposure, daily DD,
session window) so a regression points squarely at the broken rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from aether_api.mcp_client.risk import (
    AccountSnapshot,
    PositionExposure,
    RiskEnforcer,
    build_order_inputs,
)


def _project(**overrides: Any) -> SimpleNamespace:
    """Build a project stub with sensible defaults the enforcer can read."""
    base: dict[str, Any] = {
        "risk_per_trade": Decimal("1.0"),
        "max_daily_dd": Decimal("3.0"),
        "max_total_dd": Decimal("8.0"),
        "max_exposure": Decimal("10.0"),
        "trading_sessions": ["new_york", "europe"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _now_inside_session() -> datetime:
    # 2025-07-15 14:00 UTC — NY (DST) is open.
    return datetime(2025, 7, 15, 14, 0, tzinfo=UTC)


def test_missing_sl_rejected() -> None:
    project = _project()
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.1"),
        sl=Decimal("0"),
        entry_price=Decimal("1.1000"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is False
    assert "sl_missing" in result.reasons


def test_risk_per_trade_breach() -> None:
    project = _project(risk_per_trade=Decimal("0.5"))
    enforcer = RiskEnforcer()
    # risk_money_override=200 → 2% of equity, well above 0.5% cap.
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.1"),
        sl=Decimal("1.0500"),
        entry_price=Decimal("1.1000"),
        risk_money_override=Decimal("200"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is False
    assert "risk_per_trade_exceeded" in result.reasons


def test_max_exposure_breach() -> None:
    project = _project(max_exposure=Decimal("5.0"))
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("100"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
        notional_pct_of_equity=Decimal("80"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is False
    assert "max_exposure_exceeded" in result.reasons


def test_daily_dd_breach() -> None:
    project = _project(max_daily_dd=Decimal("1.0"))
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.1"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
    )
    # equity dropped 2% from balance, exceeds 1% cap.
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("9800"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is False
    assert "max_daily_dd_exceeded" in result.reasons


def test_session_closed() -> None:
    project = _project(trading_sessions=["tokyo"])  # 00:00-09:00 UTC
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.1"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=datetime(2025, 7, 15, 14, 0, tzinfo=UTC),  # outside tokyo
    )
    assert result.ok is False
    assert "session_closed" in result.reasons


def test_happy_path_approves() -> None:
    project = _project()
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.01"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
        notional_pct_of_equity=Decimal("1"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is True
    assert result.needs_approval is False
    assert result.reasons == []


def test_large_order_flags_approval() -> None:
    project = _project()
    enforcer = RiskEnforcer()
    # 0.6% risk on 1% cap → above the 0.5% half-cap threshold.
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.1"),
        sl=Decimal("1.0940"),
        entry_price=Decimal("1.1000"),
        risk_money_override=Decimal("60"),
        notional_pct_of_equity=Decimal("1"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    assert result.ok is True
    assert result.needs_approval is True


def test_exposure_sums_with_open_positions() -> None:
    project = _project(max_exposure=Decimal("5"))
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.01"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
        notional_pct_of_equity=Decimal("3"),
    )
    positions = [
        PositionExposure(
            symbol="GBPUSD",
            volume=Decimal("0.05"),
            notional_pct_of_equity=Decimal("3"),
        )
    ]
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=positions,
        now_utc=_now_inside_session(),
    )
    assert result.ok is False
    assert "max_exposure_exceeded" in result.reasons


def test_risk_check_serialises_to_dict() -> None:
    project = _project()
    enforcer = RiskEnforcer()
    inputs = build_order_inputs(
        symbol="EURUSD",
        side="buy",
        volume=Decimal("0.01"),
        sl=Decimal("1.0995"),
        entry_price=Decimal("1.1000"),
        notional_pct_of_equity=Decimal("1"),
    )
    result = enforcer.check(
        order=inputs,
        project=project,
        account=AccountSnapshot(equity=Decimal("10000"), balance=Decimal("10000")),
        positions=[],
        now_utc=_now_inside_session(),
    )
    data = result.to_dict()
    assert data["ok"] is True
    assert isinstance(data["risk_pct"], str)  # Decimal serialised to str
    assert "exposure_pct" in data
