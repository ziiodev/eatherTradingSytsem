"""Pure tool functions for the 7 live MT5 tools.

These functions are invoked by the FastMCP tool callables registered in
:mod:`mcp_metatrader5.tools.live` ``__init__``. Each function:

1. calls :meth:`MT5Bridge.ensure_ready` (gate against
   ``MT5_LIVE_ENABLED=false`` and missing binding);
2. invokes the appropriate ``MetaTrader5`` binding entry;
3. normalises the result into a strict :mod:`schemas` model;
4. raises :class:`MT5Error` with a stable :class:`ErrorCode` on any
   failure (no bare exceptions reach the MCP wire).

Charter rules enforced here (re-stated from the wrapper boundary):

* :func:`place_order` rejects ``sl in (None, 0)`` with
  :class:`ErrorCode.CHARTER_VIOLATION_MISSING_SL`. The schema layer
  already requires a positive ``sl``; this is the runtime belt to that
  suspender.
* :func:`modify_order` rejects ``sl == Decimal(0)`` with the same code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from ...config import Settings
from ...errors import ErrorCode, MT5Error
from ...logging import get_logger
from ._mt5 import get_bridge
from .schemas import (
    Candle,
    CloseOrderInput,
    CloseOrderOutput,
    DealSummary,
    GetAccountOutput,
    GetCandlesInput,
    GetCandlesOutput,
    GetHistoryInput,
    GetHistoryOutput,
    GetPositionsInput,
    GetPositionsOutput,
    ModifyOrderInput,
    ModifyOrderOutput,
    OrderSide,
    OrderType,
    PlaceOrderInput,
    PlaceOrderOutput,
    PositionSummary,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Small adapters / decoders
# ---------------------------------------------------------------------------


def _to_dec(value: Any) -> Decimal:
    """Coerce a binding float / int to :class:`Decimal` via str (no drift)."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _utc_from_ts(value: Any) -> datetime:
    """Parse an MT5 integer-seconds timestamp into a UTC ``datetime``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(int(value), tz=UTC)


def _side_from_position_type(type_int: int, mt5: Any) -> OrderSide:
    """Translate ``POSITION_TYPE_BUY`` / ``POSITION_TYPE_SELL`` to enum."""
    if type_int == getattr(mt5, "POSITION_TYPE_BUY", 0):
        return OrderSide.BUY
    if type_int == getattr(mt5, "POSITION_TYPE_SELL", 1):
        return OrderSide.SELL
    # Defensive: unknown ints surface as buy (no broker we have seen
    # returns 2 for positions; deals can, hence the optional-side schema).
    return OrderSide.BUY


def _order_type_int(*, side: OrderSide, type_: OrderType, mt5: Any) -> int:
    """Translate (side, type) to the integer MT5 expects in ``order_send``."""
    if type_ == OrderType.MARKET:
        return int(
            mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        )
    if type_ == OrderType.LIMIT:
        return int(
            mt5.ORDER_TYPE_BUY_LIMIT
            if side == OrderSide.BUY
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
    if type_ == OrderType.STOP:
        return int(
            mt5.ORDER_TYPE_BUY_STOP
            if side == OrderSide.BUY
            else mt5.ORDER_TYPE_SELL_STOP
        )
    # STOP_LIMIT
    return int(
        mt5.ORDER_TYPE_BUY_STOP_LIMIT
        if side == OrderSide.BUY
        else mt5.ORDER_TYPE_SELL_STOP_LIMIT
    )


def _retcode_ok(retcode: int, mt5: Any) -> bool:
    """Two ok retcodes: ``TRADE_RETCODE_DONE`` (10009) and ``..._DONE_PARTIAL`` (10008)."""
    ok = {
        getattr(mt5, "TRADE_RETCODE_DONE", 10009),
        getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10008),
    }
    return retcode in ok


# ---------------------------------------------------------------------------
# 1. mt5_get_account
# ---------------------------------------------------------------------------


def get_account(*, settings: Settings) -> GetAccountOutput:
    mt5 = get_bridge(settings).ensure_ready()
    info = mt5.account_info()
    if info is None:
        raise MT5Error(
            ErrorCode.MT5_CONNECT_FAILED,
            "mt5.account_info() returned None",
        )
    return GetAccountOutput(
        balance=_to_dec(info.balance),
        equity=_to_dec(info.equity),
        margin=_to_dec(info.margin),
        free_margin=_to_dec(info.margin_free),
        leverage=int(info.leverage),
        currency=str(info.currency).upper(),
        login=int(info.login),
    )


# ---------------------------------------------------------------------------
# 2. mt5_get_positions
# ---------------------------------------------------------------------------


def get_positions(
    payload: GetPositionsInput, *, settings: Settings
) -> GetPositionsOutput:
    mt5 = get_bridge(settings).ensure_ready()
    if payload.symbol is None:
        raw = mt5.positions_get()
    else:
        raw = mt5.positions_get(symbol=payload.symbol)
    if raw is None:
        raw = []

    out: list[PositionSummary] = []
    for p in raw:
        out.append(
            PositionSummary(
                ticket=int(p.ticket),
                symbol=str(p.symbol),
                side=_side_from_position_type(int(p.type), mt5),
                volume=_to_dec(p.volume),
                price_open=_to_dec(p.price_open),
                sl=_to_dec(p.sl) if p.sl else None,
                tp=_to_dec(p.tp) if p.tp else None,
                profit=_to_dec(p.profit),
                swap=_to_dec(p.swap),
                commission=_to_dec(getattr(p, "commission", 0)),
                magic=int(getattr(p, "magic", 0)),
                comment=getattr(p, "comment", None) or None,
                time=_utc_from_ts(p.time),
            )
        )
    return GetPositionsOutput(positions=out)


# ---------------------------------------------------------------------------
# 3. mt5_get_history
# ---------------------------------------------------------------------------


def get_history(payload: GetHistoryInput, *, settings: Settings) -> GetHistoryOutput:
    mt5 = get_bridge(settings).ensure_ready()
    df = payload.date_from if payload.date_from.tzinfo else payload.date_from.replace(tzinfo=UTC)
    dt = payload.date_to if payload.date_to.tzinfo else payload.date_to.replace(tzinfo=UTC)

    if payload.symbol is None:
        raw = mt5.history_deals_get(df, dt)
    else:
        raw = mt5.history_deals_get(df, dt, group=f"*{payload.symbol}*")
    if raw is None:
        raw = []

    out: list[DealSummary] = []
    for d in raw:
        out.append(
            DealSummary(
                ticket=int(d.ticket),
                order=int(getattr(d, "order", 0)),
                symbol=str(d.symbol or payload.symbol or "?"),
                side=None,  # MT5 deal types don't map 1:1 to buy/sell
                type=int(d.type),
                volume=_to_dec(d.volume),
                price=_to_dec(d.price),
                profit=_to_dec(d.profit),
                swap=_to_dec(getattr(d, "swap", 0)),
                commission=_to_dec(getattr(d, "commission", 0)),
                magic=int(getattr(d, "magic", 0)),
                time=_utc_from_ts(d.time),
                comment=getattr(d, "comment", None) or None,
            )
        )
    return GetHistoryOutput(deals=out)


# ---------------------------------------------------------------------------
# 4. mt5_get_candles
# ---------------------------------------------------------------------------

_TF_TO_MT5: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10",
    "M12": "TIMEFRAME_M12",
    "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H2": "TIMEFRAME_H2",
    "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6",
    "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def get_candles(payload: GetCandlesInput, *, settings: Settings) -> GetCandlesOutput:
    mt5 = get_bridge(settings).ensure_ready()
    tf_attr = _TF_TO_MT5.get(payload.timeframe.value)
    if tf_attr is None or not hasattr(mt5, tf_attr):
        raise MT5Error(
            ErrorCode.INVALID_INPUT,
            f"Unsupported timeframe for MT5 binding: {payload.timeframe.value}",
        )
    tf_int = getattr(mt5, tf_attr)
    rates = mt5.copy_rates_from_pos(payload.symbol, tf_int, 0, payload.count)
    if rates is None:
        raise MT5Error(
            ErrorCode.SYMBOL_NOT_FOUND,
            f"copy_rates_from_pos returned None for symbol {payload.symbol!r}",
        )
    candles: list[Candle] = []
    for r in rates:
        # MT5 returns a structured numpy array; tolerate both ndarray rows
        # and tuples by going through attribute / key access uniformly.
        get = (lambda obj, k: obj[k]) if hasattr(r, "dtype") else getattr
        candles.append(
            Candle(
                time=_utc_from_ts(get(r, "time")),
                open=_to_dec(get(r, "open")),
                high=_to_dec(get(r, "high")),
                low=_to_dec(get(r, "low")),
                close=_to_dec(get(r, "close")),
                tick_volume=int(get(r, "tick_volume")),
                spread=int(get(r, "spread")),
                real_volume=int(get(r, "real_volume")),
            )
        )
    return GetCandlesOutput(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        candles=candles,
    )


# ---------------------------------------------------------------------------
# 5. mt5_place_order — charter SL guard fires HERE
# ---------------------------------------------------------------------------


def place_order(payload: PlaceOrderInput, *, settings: Settings) -> PlaceOrderOutput:
    # Belt-and-suspenders charter check. The schema already enforces sl > 0,
    # but we re-check at the wrapper boundary so a non-strict JSON-RPC
    # client cannot smuggle a null SL through.
    if payload.sl is None or payload.sl <= 0:
        raise MT5Error(
            ErrorCode.CHARTER_VIOLATION_MISSING_SL,
            "Charter violation: every order MUST carry a non-null stop-loss. "
            "Order REJECTED before broker round-trip.",
            details={"symbol": payload.symbol, "side": payload.side.value},
        )

    mt5 = get_bridge(settings).ensure_ready()

    # Determine the fill price for market orders from the current tick.
    price: float
    if payload.type == OrderType.MARKET:
        tick = mt5.symbol_info_tick(payload.symbol)
        if tick is None:
            raise MT5Error(
                ErrorCode.SYMBOL_NOT_FOUND,
                f"symbol_info_tick returned None for {payload.symbol!r}",
            )
        price = float(tick.ask if payload.side == OrderSide.BUY else tick.bid)
    else:
        assert payload.price is not None  # schema enforces this
        price = float(payload.price)

    action = (
        mt5.TRADE_ACTION_DEAL
        if payload.type == OrderType.MARKET
        else mt5.TRADE_ACTION_PENDING
    )

    request = {
        "action": action,
        "symbol": payload.symbol,
        "volume": float(payload.volume),
        "type": _order_type_int(side=payload.side, type_=payload.type, mt5=mt5),
        "price": price,
        "sl": float(payload.sl),
        "deviation": payload.deviation,
        "magic": payload.magic,
        "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
        "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
    }
    if payload.tp is not None:
        request["tp"] = float(payload.tp)
    if payload.comment is not None:
        request["comment"] = payload.comment

    result = mt5.order_send(request)
    if result is None:
        raise MT5Error(
            ErrorCode.ORDER_REJECTED,
            "mt5.order_send() returned None",
        )
    retcode = int(result.retcode)
    if not _retcode_ok(retcode, mt5):
        raise MT5Error(
            _retcode_to_error(retcode, mt5),
            f"mt5.order_send rejected: retcode={retcode}",
            details={"comment": getattr(result, "comment", None)},
            mt5_retcode=retcode,
        )

    ticket = int(getattr(result, "order", 0) or getattr(result, "deal", 0))
    filled_price = _to_dec(getattr(result, "price", 0))
    filled_vol = _to_dec(getattr(result, "volume", 0))
    status: Literal["filled", "placed"] = (
        "filled" if payload.type == OrderType.MARKET else "placed"
    )
    return PlaceOrderOutput(
        ticket=ticket,
        status=status,
        filled_price=filled_price if filled_price else None,
        filled_volume=filled_vol if filled_vol else None,
        mt5_retcode=retcode,
    )


def _retcode_to_error(retcode: int, mt5: Any) -> ErrorCode:
    """Map an MT5 retcode to a stable :class:`ErrorCode`."""
    invalid_volume = getattr(mt5, "TRADE_RETCODE_INVALID_VOLUME", 10014)
    invalid_stops = getattr(mt5, "TRADE_RETCODE_INVALID_STOPS", 10016)
    if retcode == invalid_volume:
        return ErrorCode.INVALID_VOLUME
    if retcode == invalid_stops:
        return ErrorCode.INVALID_STOPS
    return ErrorCode.ORDER_REJECTED


# ---------------------------------------------------------------------------
# 6. mt5_modify_order — SL removal blocked HERE
# ---------------------------------------------------------------------------


def modify_order(payload: ModifyOrderInput, *, settings: Settings) -> ModifyOrderOutput:
    if payload.sl is not None and payload.sl == 0:
        raise MT5Error(
            ErrorCode.CHARTER_VIOLATION_MISSING_SL,
            "Charter violation: stop-loss cannot be cleared. Pass a positive "
            "sl value or omit sl from the request to keep the current SL.",
            details={"ticket": payload.ticket},
        )

    mt5 = get_bridge(settings).ensure_ready()

    # Find the position so we know its symbol — TRADE_ACTION_SLTP requires it.
    positions = mt5.positions_get(ticket=payload.ticket) or []
    if not positions:
        # Could also be an *order* (pending). Try orders_get.
        orders = mt5.orders_get(ticket=payload.ticket) or []
        if not orders:
            raise MT5Error(
                ErrorCode.TICKET_NOT_FOUND,
                f"ticket {payload.ticket} not found among positions or pending orders",
            )
        target = orders[0]
        action = mt5.TRADE_ACTION_MODIFY
    else:
        target = positions[0]
        action = mt5.TRADE_ACTION_SLTP

    request: dict[str, Any] = {
        "action": action,
        "position": payload.ticket if action == mt5.TRADE_ACTION_SLTP else None,
        "order": payload.ticket if action == mt5.TRADE_ACTION_MODIFY else None,
        "symbol": str(target.symbol),
    }
    # The current values are preserved when the caller omits a field.
    new_sl = float(payload.sl) if payload.sl is not None else float(target.sl or 0)
    new_tp = float(payload.tp) if payload.tp is not None else float(target.tp or 0)
    request["sl"] = new_sl
    request["tp"] = new_tp
    # Strip None keys (action determines which of position/order applies).
    request = {k: v for k, v in request.items() if v is not None}

    result = mt5.order_send(request)
    if result is None:
        raise MT5Error(
            ErrorCode.ORDER_REJECTED,
            "mt5.order_send() returned None on modify",
        )
    retcode = int(result.retcode)
    if not _retcode_ok(retcode, mt5):
        raise MT5Error(
            _retcode_to_error(retcode, mt5),
            f"modify rejected: retcode={retcode}",
            details={"comment": getattr(result, "comment", None)},
            mt5_retcode=retcode,
        )
    return ModifyOrderOutput(
        ticket=payload.ticket,
        sl=_to_dec(new_sl) if new_sl else None,
        tp=_to_dec(new_tp) if new_tp else None,
        mt5_retcode=retcode,
    )


# ---------------------------------------------------------------------------
# 7. mt5_close_order
# ---------------------------------------------------------------------------


def close_order(payload: CloseOrderInput, *, settings: Settings) -> CloseOrderOutput:
    mt5 = get_bridge(settings).ensure_ready()
    positions = mt5.positions_get(ticket=payload.ticket) or []
    if not positions:
        raise MT5Error(
            ErrorCode.TICKET_NOT_FOUND,
            f"position {payload.ticket} not found",
        )
    pos = positions[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        raise MT5Error(
            ErrorCode.SYMBOL_NOT_FOUND,
            f"symbol_info_tick returned None for {pos.symbol!r}",
        )

    pos_side = _side_from_position_type(int(pos.type), mt5)
    close_type = (
        mt5.ORDER_TYPE_SELL if pos_side == OrderSide.BUY else mt5.ORDER_TYPE_BUY
    )
    price = float(tick.bid if pos_side == OrderSide.BUY else tick.ask)

    volume = float(payload.volume) if payload.volume is not None else float(pos.volume)

    request: dict[str, Any] = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": str(pos.symbol),
        "volume": volume,
        "type": close_type,
        "position": int(pos.ticket),
        "price": price,
        "deviation": payload.deviation,
        "magic": int(getattr(pos, "magic", 0)),
        "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
        "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
    }
    if payload.comment is not None:
        request["comment"] = payload.comment

    result = mt5.order_send(request)
    if result is None:
        raise MT5Error(
            ErrorCode.ORDER_REJECTED,
            "mt5.order_send() returned None on close",
        )
    retcode = int(result.retcode)
    if not _retcode_ok(retcode, mt5):
        raise MT5Error(
            _retcode_to_error(retcode, mt5),
            f"close rejected: retcode={retcode}",
            details={"comment": getattr(result, "comment", None)},
            mt5_retcode=retcode,
        )
    return CloseOrderOutput(
        closed_ticket=int(getattr(result, "order", 0) or pos.ticket),
        close_price=_to_dec(getattr(result, "price", price)),
        closed_volume=_to_dec(volume),
        mt5_retcode=retcode,
    )


__all__ = [
    "close_order",
    "get_account",
    "get_candles",
    "get_history",
    "get_positions",
    "modify_order",
    "place_order",
]
