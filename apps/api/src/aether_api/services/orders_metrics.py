"""Pure-Python aggregation primitives over a list of :class:`Order` rows.

The Operativa surface needs four headline metrics over a closed-trades
slice:

* **trades_total** — count of rows considered.
* **win_rate** — fraction of trades whose ``profit_net`` is strictly
  positive.
* **profit_factor** — ``sum(wins) / sum(|losses|)``. Returns the string
  ``"Infinity"`` when there are wins but zero losses (the spec wants
  the wire / JSON representation to be a stable token, not ``NaN`` /
  ``null``). Returns ``0.0`` when both wins and losses are zero.
* **avg_rr** — average realised R-multiple across trades that have a
  defined R denominator. Buys: ``(close - open) / abs(open - sl)``;
  sells: ``-(close - open) / abs(open - sl)``. Rows with ``sl is None``
  OR ``open_price == sl`` are excluded (denominator would be zero /
  undefined). Returns ``None`` when no trade has a valid R.
* **total_pnl** — sum of ``profit_net`` (``Decimal``).

These functions are intentionally pure: they consume an iterable of
ORM rows (or any duck-typed object exposing the same attributes) and
return scalars. The repository layer composes them on top of a tenant-
scoped query — see :mod:`aether_api.repositories.order_repository`.

Rows whose ``profit_net`` is ``None`` are skipped by every aggregation
that needs P&L. Rows whose ``open_price`` / ``close_price`` / ``sl`` are
missing skip only the avg_rr calculation.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

# We deliberately type the public API as ``Iterable[Any]`` rather than a
# strict :class:`typing.Protocol`. The metric primitives need to consume
# both the real :class:`aether_api.models.order.Order` ORM rows (whose
# ``sl`` column is ``Decimal`` non-optional) AND lightweight test
# dataclasses (whose ``sl`` is ``Decimal | None``). Protocol attribute
# variance under mypy rejects passing the former where the latter is
# declared; ``Any`` is the cleanest opt-out without leaking either shape.
_OrderLike = Any


def _to_decimal(value: Decimal | int | float | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def win_rate(orders: Iterable[_OrderLike]) -> float:
    """Fraction of trades whose ``profit_net`` is > 0.

    Trades with ``profit_net is None`` are skipped entirely (they do
    not count as wins or losses; they're typically still-open or
    never-filled rows). Returns ``0.0`` when the considered set is
    empty.
    """
    total = 0
    wins = 0
    for o in orders:
        pnl = _to_decimal(o.profit_net)
        if pnl is None:
            continue
        total += 1
        if pnl > 0:
            wins += 1
    if total == 0:
        return 0.0
    return wins / total


def profit_factor(orders: Iterable[_OrderLike]) -> float | str:
    """``sum(wins) / sum(|losses|)``.

    Returns the literal string ``"Infinity"`` when the slice has wins
    but zero losses (the wire contract is a stable JSON-safe token).
    Returns ``0.0`` when both sums are zero (no trades, or all trades
    flat).
    """
    gross_win = Decimal("0")
    gross_loss = Decimal("0")
    for o in orders:
        pnl = _to_decimal(o.profit_net)
        if pnl is None:
            continue
        if pnl > 0:
            gross_win += pnl
        elif pnl < 0:
            gross_loss += -pnl
    if gross_loss == 0:
        if gross_win == 0:
            return 0.0
        return "Infinity"
    return float(gross_win / gross_loss)


def avg_rr(orders: Iterable[_OrderLike]) -> float | None:
    """Mean realised R-multiple across trades with a valid R denominator.

    Per-trade R is:

    * buys:  ``(close - open) / abs(open - sl)``
    * sells: ``-(close - open) / abs(open - sl)``

    A trade is excluded from the mean when ``sl is None``, when
    ``open_price`` or ``close_price`` is missing, or when
    ``open_price == sl`` (zero denominator).

    Returns ``None`` when no trade has a valid R — the caller should
    surface this as ``null`` rather than ``0`` to distinguish "no
    data" from "average is zero".
    """
    rs: list[Decimal] = []
    for o in orders:
        sl = _to_decimal(o.sl)
        op_price = _to_decimal(o.open_price)
        cl_price = _to_decimal(o.close_price)
        if sl is None or op_price is None or cl_price is None:
            continue
        denom = abs(op_price - sl)
        if denom == 0:
            continue
        delta = cl_price - op_price
        if o.side == "sell":
            delta = -delta
        rs.append(delta / denom)
    if not rs:
        return None
    return float(sum(rs) / Decimal(len(rs)))


def total_pnl(orders: Iterable[_OrderLike]) -> Decimal:
    """Sum of ``profit_net`` across the slice. ``None`` values skipped."""
    total = Decimal("0")
    for o in orders:
        pnl = _to_decimal(o.profit_net)
        if pnl is None:
            continue
        total += pnl
    return total


__all__ = ["avg_rr", "profit_factor", "total_pnl", "win_rate"]
