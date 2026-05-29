"""Unit tests for :mod:`aether_api.services.orders_metrics`.

Pure-Python primitives — no DB, no fixtures, no event loop. Each test
seeds a synthetic order list (via a small dataclass that quacks like
:class:`Order`) and asserts the metric matches the formula given in
the spec.

Edge cases covered:

* All-win slice → ``profit_factor == "Infinity"``.
* Empty slice → ``win_rate == 0.0``, ``profit_factor == 0.0``,
  ``avg_rr is None``, ``total_pnl == 0``.
* Mixed buys + sells with valid SL → ``avg_rr`` averages signed
  R-multiples correctly.
* ``open_price == sl`` and ``sl is None`` rows → excluded from
  ``avg_rr`` mean.
* ``profit_net is None`` rows → skipped by every aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aether_api.services.orders_metrics import (
    avg_rr,
    profit_factor,
    total_pnl,
    win_rate,
)


@dataclass
class _FakeOrder:
    """Minimal shape :mod:`orders_metrics` accepts.

    Fields mirror the :class:`aether_api.models.order.Order` attributes
    the metric functions read.
    """

    side: str = "buy"
    sl: Decimal | None = None
    open_price: Decimal | None = None
    close_price: Decimal | None = None
    profit_net: Decimal | None = None


# ---------------------------------------------------------------------------
# win_rate
# ---------------------------------------------------------------------------
def test_win_rate_empty_returns_zero() -> None:
    assert win_rate([]) == 0.0


def test_win_rate_all_wins() -> None:
    orders = [
        _FakeOrder(profit_net=Decimal("10")),
        _FakeOrder(profit_net=Decimal("20")),
    ]
    assert win_rate(orders) == 1.0


def test_win_rate_mixed() -> None:
    orders = [
        _FakeOrder(profit_net=Decimal("10")),
        _FakeOrder(profit_net=Decimal("-5")),
        _FakeOrder(profit_net=Decimal("3")),
        _FakeOrder(profit_net=Decimal("-1")),
    ]
    assert win_rate(orders) == 0.5


def test_win_rate_skips_none_profit() -> None:
    """``profit_net is None`` rows are neither wins nor counted in the denominator."""
    orders = [
        _FakeOrder(profit_net=None),
        _FakeOrder(profit_net=Decimal("10")),
    ]
    # 1/1 = 1.0, not 1/2.
    assert win_rate(orders) == 1.0


def test_win_rate_flat_trades_counted_as_loss() -> None:
    """A trade with profit_net == 0 is counted but does NOT win."""
    orders = [
        _FakeOrder(profit_net=Decimal("0")),
        _FakeOrder(profit_net=Decimal("10")),
    ]
    assert win_rate(orders) == 0.5


# ---------------------------------------------------------------------------
# profit_factor
# ---------------------------------------------------------------------------
def test_profit_factor_empty_returns_zero() -> None:
    assert profit_factor([]) == 0.0


def test_profit_factor_no_losses_returns_infinity_string() -> None:
    orders = [
        _FakeOrder(profit_net=Decimal("10")),
        _FakeOrder(profit_net=Decimal("5")),
    ]
    assert profit_factor(orders) == "Infinity"


def test_profit_factor_basic() -> None:
    """``sum(wins) / sum(|losses|)``."""
    orders = [
        _FakeOrder(profit_net=Decimal("30")),
        _FakeOrder(profit_net=Decimal("-10")),
        _FakeOrder(profit_net=Decimal("-5")),
    ]
    # 30 / 15 = 2.0
    assert profit_factor(orders) == 2.0


def test_profit_factor_only_losses_returns_zero() -> None:
    """No wins → ``gross_win = 0`` → ``0.0`` (not Infinity)."""
    orders = [
        _FakeOrder(profit_net=Decimal("-10")),
        _FakeOrder(profit_net=Decimal("-5")),
    ]
    # gross_win=0, gross_loss=15 → 0/15 == 0.0
    assert profit_factor(orders) == 0.0


def test_profit_factor_ignores_none() -> None:
    orders = [
        _FakeOrder(profit_net=None),
        _FakeOrder(profit_net=Decimal("20")),
        _FakeOrder(profit_net=Decimal("-10")),
    ]
    assert profit_factor(orders) == 2.0


# ---------------------------------------------------------------------------
# avg_rr
# ---------------------------------------------------------------------------
def test_avg_rr_empty_returns_none() -> None:
    assert avg_rr([]) is None


def test_avg_rr_buy_single() -> None:
    """Buy: (close - open) / abs(open - sl)."""
    orders = [
        _FakeOrder(
            side="buy",
            sl=Decimal("1.0900"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1050"),
        )
    ]
    # delta = 0.0050; denom = 0.0100; r = 0.5
    assert avg_rr(orders) == 0.5


def test_avg_rr_sell_single() -> None:
    """Sell: -(close - open) / abs(open - sl)."""
    orders = [
        _FakeOrder(
            side="sell",
            sl=Decimal("1.1100"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.0950"),
        )
    ]
    # delta_raw = -0.0050; sell negates → +0.0050; denom = 0.0100; r = 0.5
    assert avg_rr(orders) == 0.5


def test_avg_rr_mixed_buys_and_sells() -> None:
    """Mean of signed R-multiples across both sides."""
    orders = [
        _FakeOrder(  # +0.5R buy
            side="buy",
            sl=Decimal("1.0900"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1050"),
        ),
        _FakeOrder(  # -1R sell (close above open == loss)
            side="sell",
            sl=Decimal("1.1100"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1100"),
        ),
    ]
    # rs = [0.5, -1.0] → mean -0.25
    assert avg_rr(orders) == -0.25


def test_avg_rr_excludes_missing_sl() -> None:
    """Rows with sl == None do not participate in the mean."""
    orders = [
        _FakeOrder(  # excluded — sl is None
            side="buy",
            sl=None,
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1100"),
        ),
        _FakeOrder(  # +1R
            side="buy",
            sl=Decimal("1.0900"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1100"),
        ),
    ]
    assert avg_rr(orders) == 1.0


def test_avg_rr_excludes_zero_denominator() -> None:
    """open_price == sl → denominator is zero → excluded."""
    orders = [
        _FakeOrder(  # excluded
            side="buy",
            sl=Decimal("1.1000"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1100"),
        ),
        _FakeOrder(  # +1R
            side="buy",
            sl=Decimal("1.0900"),
            open_price=Decimal("1.1000"),
            close_price=Decimal("1.1100"),
        ),
    ]
    assert avg_rr(orders) == 1.0


def test_avg_rr_all_invalid_returns_none() -> None:
    """If every row is excluded, return None — not 0.0."""
    orders = [
        _FakeOrder(side="buy", sl=None),
        _FakeOrder(
            side="buy",
            sl=Decimal("1.1000"),
            open_price=Decimal("1.1000"),  # denom = 0
            close_price=Decimal("1.1100"),
        ),
    ]
    assert avg_rr(orders) is None


def test_avg_rr_excludes_missing_close() -> None:
    """Open positions (close_price is None) are excluded."""
    orders = [
        _FakeOrder(
            side="buy",
            sl=Decimal("1.0900"),
            open_price=Decimal("1.1000"),
            close_price=None,  # still open
        ),
    ]
    assert avg_rr(orders) is None


# ---------------------------------------------------------------------------
# total_pnl
# ---------------------------------------------------------------------------
def test_total_pnl_empty_returns_zero() -> None:
    assert total_pnl([]) == Decimal("0")


def test_total_pnl_sums_profit_net() -> None:
    orders = [
        _FakeOrder(profit_net=Decimal("10")),
        _FakeOrder(profit_net=Decimal("-3.50")),
        _FakeOrder(profit_net=Decimal("2.25")),
    ]
    assert total_pnl(orders) == Decimal("8.75")


def test_total_pnl_skips_none() -> None:
    orders = [
        _FakeOrder(profit_net=None),
        _FakeOrder(profit_net=Decimal("4")),
    ]
    assert total_pnl(orders) == Decimal("4")
