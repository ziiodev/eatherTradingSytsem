"""Pydantic input/output schemas for the 7 live-trading tools.

Strict (``extra="forbid"``) like the existing backtest tool schemas. Numbers
that represent prices and account values are passed as :class:`Decimal`
to avoid float drift — the wrapper converts to ``float`` only at the
``MetaTrader5`` binding boundary, where the API takes ``float`` anyway.

Charter invariants captured in this schema layer:

* :class:`PlaceOrderInput.sl` is **required** and non-zero. Pydantic
  enforces the field is set; the tool layer additionally raises
  :class:`ErrorCode.CHARTER_VIOLATION_MISSING_SL` so the agent gets a
  programmatic code (not a 422 from FastMCP).
* :class:`ModifyOrderInput.sl` is optional but, if present, must be a
  positive :class:`Decimal`. Passing ``sl=0`` or ``sl=None`` to *clear*
  the stop is rejected at the tool boundary (also
  ``CHARTER_VIOLATION_MISSING_SL``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..schemas import Timeframe

# ---------------------------------------------------------------------------
# Shared base / aliases
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Same shape as ``tools/schemas._StrictModel`` — re-declared so the live
    subpackage doesn't have to import a private symbol from its sibling.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


_Symbol = Annotated[
    str,
    StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
]
_Comment = Annotated[
    str | None,
    StringConstraints(max_length=255),
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OrderSide(StrEnum):
    """Direction of an order — ``buy`` opens long, ``sell`` opens short."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Order kind. ``market`` is the only synchronous one; the rest are
    pending orders that the broker queues until the trigger hits.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class PositionSummary(_StrictModel):
    """Slim representation of one open MT5 position."""

    ticket: int = Field(ge=0)
    symbol: _Symbol
    side: OrderSide
    volume: Decimal
    price_open: Decimal
    sl: Decimal | None = None
    tp: Decimal | None = None
    profit: Decimal
    swap: Decimal
    commission: Decimal
    magic: int = 0
    comment: str | None = None
    time: datetime


class DealSummary(_StrictModel):
    """One historical deal row returned by ``mt5_get_history``."""

    ticket: int = Field(ge=0)
    order: int = Field(ge=0)
    symbol: _Symbol
    side: OrderSide | None = None  # MT5 'in'/'out' deals do not always map cleanly
    type: int = Field(ge=0, description="Raw MT5 deal type integer.")
    volume: Decimal
    price: Decimal
    profit: Decimal
    swap: Decimal
    commission: Decimal
    magic: int = 0
    time: datetime
    comment: str | None = None


class Candle(_StrictModel):
    """One OHLCV bar."""

    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int = Field(ge=0)
    spread: int = Field(ge=0)
    real_volume: int = Field(ge=0)


# ---------------------------------------------------------------------------
# 1. mt5_get_account
# ---------------------------------------------------------------------------


class GetAccountInput(_StrictModel):
    """No inputs — the active terminal's connection is the implicit scope."""


class GetAccountOutput(_StrictModel):
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    leverage: int = Field(ge=1, le=10_000)
    currency: Annotated[
        str,
        StringConstraints(min_length=2, max_length=10, pattern=r"^[A-Z]{2,10}$"),
    ]
    login: int = Field(ge=1)


# ---------------------------------------------------------------------------
# 2. mt5_get_positions
# ---------------------------------------------------------------------------


class GetPositionsInput(_StrictModel):
    symbol: _Symbol | None = None


class GetPositionsOutput(_StrictModel):
    positions: list[PositionSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. mt5_get_history
# ---------------------------------------------------------------------------


class GetHistoryInput(_StrictModel):
    date_from: datetime = Field(description="UTC start of the window (inclusive).")
    date_to: datetime = Field(description="UTC end of the window (inclusive).")
    symbol: _Symbol | None = None

    @model_validator(mode="after")
    def _check_window(self) -> GetHistoryInput:
        if self.date_to < self.date_from:
            raise ValueError(
                f"date_to ({self.date_to}) must be on or after date_from ({self.date_from})"
            )
        return self


class GetHistoryOutput(_StrictModel):
    deals: list[DealSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. mt5_get_candles
# ---------------------------------------------------------------------------


class GetCandlesInput(_StrictModel):
    symbol: _Symbol
    timeframe: Timeframe
    count: int = Field(gt=0, le=10_000, description="Number of bars to return (most recent first).")


class GetCandlesOutput(_StrictModel):
    symbol: _Symbol
    timeframe: Timeframe
    candles: list[Candle] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. mt5_place_order
# ---------------------------------------------------------------------------


class PlaceOrderInput(_StrictModel):
    symbol: _Symbol
    side: OrderSide
    type: OrderType = OrderType.MARKET
    volume: Decimal = Field(gt=0, description="Volume in lots.")
    #: MANDATORY (charter rule). The model would accept ``None`` only if we
    #: explicitly opted in — we do NOT. The wrapper additionally raises
    #: ``CHARTER_VIOLATION_MISSING_SL`` if a caller somehow bypasses the
    #: schema (e.g. via a non-strict JSON-RPC client).
    sl: Decimal = Field(gt=0, description="Stop-loss price. MANDATORY.")
    tp: Decimal | None = Field(default=None, gt=0, description="Take-profit price.")
    price: Decimal | None = Field(
        default=None,
        gt=0,
        description="Limit/stop price for non-market orders. Ignored for market orders.",
    )
    deviation: int = Field(default=20, ge=0, le=10_000, description="Slippage in points.")
    magic: int = Field(default=0, ge=0, le=2**31 - 1)
    comment: _Comment = None

    @model_validator(mode="after")
    def _price_required_for_pending(self) -> PlaceOrderInput:
        if self.type != OrderType.MARKET and self.price is None:
            raise ValueError(
                f"price is required for non-market orders (got type={self.type.value})"
            )
        return self


class PlaceOrderOutput(_StrictModel):
    ticket: int = Field(ge=0, description="Broker-assigned order ticket.")
    status: Literal["filled", "placed"] = "filled"
    filled_price: Decimal | None = None
    filled_volume: Decimal | None = None
    mt5_retcode: int = Field(description="Raw MT5 retcode (10009/10008 == ok).")


# ---------------------------------------------------------------------------
# 6. mt5_modify_order
# ---------------------------------------------------------------------------


class ModifyOrderInput(_StrictModel):
    ticket: int = Field(ge=0)
    #: Optional — only updated if provided. The wrapper REJECTS ``Decimal(0)``
    #: because that would effectively clear the SL (charter violation).
    sl: Decimal | None = Field(default=None, ge=0)
    tp: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> ModifyOrderInput:
        if self.sl is None and self.tp is None:
            raise ValueError("modify_order must include at least one of sl or tp")
        return self


class ModifyOrderOutput(_StrictModel):
    ticket: int = Field(ge=0)
    sl: Decimal | None = None
    tp: Decimal | None = None
    mt5_retcode: int


# ---------------------------------------------------------------------------
# 7. mt5_close_order
# ---------------------------------------------------------------------------


class CloseOrderInput(_StrictModel):
    ticket: int = Field(ge=0)
    volume: Decimal | None = Field(
        default=None,
        gt=0,
        description="Partial close volume; omit to close the entire position.",
    )
    deviation: int = Field(default=20, ge=0, le=10_000)
    comment: _Comment = None


class CloseOrderOutput(_StrictModel):
    closed_ticket: int = Field(ge=0)
    close_price: Decimal | None = None
    closed_volume: Decimal | None = None
    mt5_retcode: int


__all__ = [
    "Candle",
    "CloseOrderInput",
    "CloseOrderOutput",
    "DealSummary",
    "GetAccountInput",
    "GetAccountOutput",
    "GetCandlesInput",
    "GetCandlesOutput",
    "GetHistoryInput",
    "GetHistoryOutput",
    "GetPositionsInput",
    "GetPositionsOutput",
    "ModifyOrderInput",
    "ModifyOrderOutput",
    "OrderSide",
    "OrderType",
    "PlaceOrderInput",
    "PlaceOrderOutput",
    "PositionSummary",
]
