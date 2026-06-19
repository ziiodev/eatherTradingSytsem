"""Pure-stdlib indicator helper SOURCE emitted into the generated Python script.

These are NOT runtime helpers used by the codegen engine — they are text blocks
spliced verbatim into the generated standalone script. Every function below uses
ONLY the Python standard library (``math`` plus plain lists), so the emitted
program has ZERO third-party dependencies. Each function returns a list aligned
1:1 with the input ``bars`` (index ``i`` = bar ``i``); positions that lack enough
history to compute a value hold ``None`` (the warmup window).

Split across :mod:`indicators_osc` (RSI/MACD/Stochastic/ZScore) to keep both
files under the ~150-line limit. :data:`INDICATOR_LIB` joins all blocks in a
deterministic order for the skeleton.
"""

from __future__ import annotations

from aether_api.services.codegen.python.indicators_osc import (
    MACD_SRC,
    RSI_SRC,
    STDDEV_SRC,
    STOCH_SRC,
    ZSCORE_SRC,
)

# Simple moving average over ``period`` bars (population mean of the window).
SMA_SRC = '''def ind_sma(values, period):
    """SMA of ``values`` over ``period`` bars; None until the window fills."""
    out = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out'''

# Exponential moving average (Wilder-independent, classic 2/(n+1) smoothing).
EMA_SRC = '''def ind_ema(values, period):
    """EMA of ``values`` over ``period`` bars seeded with the first SMA."""
    out = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out'''

# Applied-price selector: map an MQL5 PRICE_* constant to a per-bar value list.
PRICE_SRC = '''def ind_price(bars, applied_price):
    """Return the per-bar price series for an MQL5-style PRICE_* constant."""
    table = {
        "PRICE_CLOSE": lambda b: b.close,
        "PRICE_OPEN": lambda b: b.open,
        "PRICE_HIGH": lambda b: b.high,
        "PRICE_LOW": lambda b: b.low,
        "PRICE_MEDIAN": lambda b: (b.high + b.low) / 2.0,
        "PRICE_TYPICAL": lambda b: (b.high + b.low + b.close) / 3.0,
        "PRICE_WEIGHTED": lambda b: (b.high + b.low + 2.0 * b.close) / 4.0,
    }
    pick = table.get(applied_price, table["PRICE_CLOSE"])
    return [pick(b) for b in bars]'''


# Deterministic concatenation of every indicator helper block, separated by two
# blank lines (PEP 8 top-level spacing) so the emitted script stays readable.
INDICATOR_LIB = "\n\n\n".join(
    [
        PRICE_SRC,
        SMA_SRC,
        EMA_SRC,
        STDDEV_SRC,
        RSI_SRC,
        MACD_SRC,
        STOCH_SRC,
        ZSCORE_SRC,
    ]
)
