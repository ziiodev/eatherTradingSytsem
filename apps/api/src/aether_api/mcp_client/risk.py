"""RiskEnforcer — pure-Python risk gate for live orders.

The enforcer is **the** authoritative defence layer for the charter risk
caps. It runs BEFORE the MCP call, takes the project's risk-config row
+ the current account snapshot + open positions, and returns a
JSON-serializable :class:`RiskCheckResult` that:

* Rejects (``ok=False``) the order when any rule fires.
* Otherwise approves it (``ok=True``), possibly flagging it as
  ``needs_approval`` for the ApprovalGate downstream.

Rules (in order):

1. **Mandatory SL** — the order's ``sl`` must be a positive Decimal.
   Charter invariant. Belt to the wrapper's suspender.
2. **Risk per trade** — ``risk_money / equity ≤ project.risk_per_trade``.
   ``risk_money`` is computed from ``volume × |entry − sl| × point_value``
   when the project carries one; otherwise we trust the caller to pass
   ``risk_money_override``.
3. **Max exposure** — ``current_exposure + this_order_exposure ≤
   project.max_exposure``. Exposure is the percentage of equity
   committed to open positions.
4. **Daily DD** — if equity has dropped more than ``project.max_daily_dd``
   from ``balance`` the gate refuses.
5. **Trading sessions** — current UTC time must fall in the union of
   ``project.trading_sessions``.
6. **Large order** — when ``risk_money / equity > 0.5 * risk_per_trade``
   OR ``this_order_exposure > 0.5 * max_exposure``, flag
   ``needs_approval=True``. The Worker passes that through the
   :class:`aether_api.mcp_client.approvals.ApprovalGate`.

The enforcer is **pure** — no DB, no IO, no clock. The caller passes in
``now_utc`` so tests can freeze time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aether_api.models.order import Order
from aether_api.models.project import Project

from .sessions import is_session_open


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """The slice of ``mt5_get_account`` the enforcer needs."""

    equity: Decimal
    balance: Decimal


@dataclass(frozen=True, slots=True)
class PositionExposure:
    """Slice of an open position the enforcer needs."""

    symbol: str
    volume: Decimal
    notional_pct_of_equity: Decimal


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """Outcome of :meth:`RiskEnforcer.check` — JSON-serializable."""

    ok: bool
    needs_approval: bool = False
    reasons: list[str] = field(default_factory=list)
    risk_pct: Decimal | None = None
    exposure_pct: Decimal | None = None
    daily_dd_pct: Decimal | None = None
    session_open: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        # Decimals are JSON-encoded as strings to preserve precision; the
        # frontend coerces back to Number when rendering.
        data = asdict(self)
        for key in ("risk_pct", "exposure_pct", "daily_dd_pct"):
            if isinstance(data[key], Decimal):
                data[key] = str(data[key])
        return data


@dataclass(frozen=True, slots=True)
class _OrderInputs:
    """Slice of the inbound order DTO the enforcer needs.

    The router converts its DTO into this struct so the enforcer signature
    does not depend on the routers package (no circular imports).
    """

    symbol: str
    side: str
    volume: Decimal
    sl: Decimal
    entry_price: Decimal
    point_value: Decimal | None = None
    risk_money_override: Decimal | None = None
    notional_pct_of_equity: Decimal | None = None


class RiskEnforcer:
    """Pure risk gate. Construct fresh per request."""

    def __init__(self) -> None:
        pass

    def check(
        self,
        *,
        order: _OrderInputs,
        project: Project,
        account: AccountSnapshot,
        positions: list[PositionExposure],
        now_utc: datetime,
    ) -> RiskCheckResult:
        reasons: list[str] = []

        # --- Rule 1: mandatory SL ---------------------------------------
        if order.sl is None or order.sl <= 0:
            reasons.append("sl_missing")
            return RiskCheckResult(ok=False, reasons=reasons)

        equity = account.equity
        if equity <= 0:
            reasons.append("equity_non_positive")
            return RiskCheckResult(ok=False, reasons=reasons)

        # --- Rule 2: risk per trade -------------------------------------
        if order.risk_money_override is not None:
            risk_money = order.risk_money_override
        elif order.point_value is not None and order.entry_price > 0:
            risk_money = (abs(order.entry_price - order.sl) * order.volume) * order.point_value
        else:
            # Fall back to volume * |entry-sl| (currency-naive; conservative).
            risk_money = abs(order.entry_price - order.sl) * order.volume

        risk_pct = (risk_money / equity) * Decimal("100")
        risk_cap = project.risk_per_trade or Decimal("1.0")
        if risk_pct > risk_cap:
            reasons.append("risk_per_trade_exceeded")

        # --- Rule 3: max exposure ---------------------------------------
        current_exposure = sum(
            (p.notional_pct_of_equity for p in positions),
            start=Decimal("0"),
        )
        order_exposure = (
            order.notional_pct_of_equity
            if order.notional_pct_of_equity is not None
            else (order.volume * order.entry_price / equity) * Decimal("100")
        )
        total_exposure = current_exposure + order_exposure
        max_exposure = project.max_exposure or Decimal("10.0")
        if total_exposure > max_exposure:
            reasons.append("max_exposure_exceeded")

        # --- Rule 4: daily DD -------------------------------------------
        # DD is the percentage drop of equity from balance (today's high-
        # water mark). Conservative — does not net intraday peaks.
        if account.balance > 0:
            dd_pct = ((account.balance - equity) / account.balance) * Decimal("100")
        else:
            dd_pct = Decimal("0")
        max_dd = project.max_daily_dd or Decimal("3.0")
        if dd_pct > max_dd:
            reasons.append("max_daily_dd_exceeded")

        # --- Rule 5: trading sessions -----------------------------------
        session_open = is_session_open(list(project.trading_sessions or []), now_utc)
        if not session_open:
            reasons.append("session_closed")

        ok = not reasons

        # --- Rule 6: large order — only meaningful when ok=True ---------
        needs_approval = False
        if ok:
            half_risk = risk_cap / Decimal("2")
            half_expo = max_exposure / Decimal("2")
            if risk_pct > half_risk or order_exposure > half_expo:
                needs_approval = True

        return RiskCheckResult(
            ok=ok,
            needs_approval=needs_approval,
            reasons=reasons,
            risk_pct=risk_pct.quantize(Decimal("0.0001")),
            exposure_pct=total_exposure.quantize(Decimal("0.0001")),
            daily_dd_pct=dd_pct.quantize(Decimal("0.0001")),
            session_open=session_open,
        )


def build_order_inputs(
    *,
    symbol: str,
    side: str,
    volume: Decimal,
    sl: Decimal,
    entry_price: Decimal,
    point_value: Decimal | None = None,
    risk_money_override: Decimal | None = None,
    notional_pct_of_equity: Decimal | None = None,
) -> _OrderInputs:
    """Public constructor for :class:`_OrderInputs` — keeps the dataclass private."""
    return _OrderInputs(
        symbol=symbol,
        side=side,
        volume=volume,
        sl=sl,
        entry_price=entry_price,
        point_value=point_value,
        risk_money_override=risk_money_override,
        notional_pct_of_equity=notional_pct_of_equity,
    )


__all__ = [
    "AccountSnapshot",
    "PositionExposure",
    "RiskCheckResult",
    "RiskEnforcer",
    "build_order_inputs",
]

# Reference imports kept at module bottom to confirm typed binding
# without re-exporting privately-named helpers above.
_ = Order  # ensure the module is importable when the model is not yet loaded
