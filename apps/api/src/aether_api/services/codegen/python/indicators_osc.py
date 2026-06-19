"""Oscillator / dispersion indicator SOURCE blocks (pure stdlib).

Split out of :mod:`indicators` to keep each module under the ~150-line limit.
Every block uses only the standard library. Numeric methods match the MQL5
reference semantics: Wilder RSI smoothing, EMA-based MACD main line, rolling
Stochastic %K/%D, and population standard deviation with the optional Bessel
sample correction that mirrors ``nodes/zscore.py``.
"""

from __future__ import annotations

# Wilder's RSI: seed the first average over ``period`` deltas, then smooth.
RSI_SRC = '''def ind_rsi(closes, period):
    """Wilder RSI of ``closes`` over ``period``; None until seeded."""
    out = [None] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain, avg_loss):
    """Convert smoothed average gain/loss into an RSI reading in [0, 100]."""
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))'''

# MACD main line: fast EMA minus slow EMA of the applied price.
MACD_SRC = '''def ind_macd(values, fast_period, slow_period, signal_period):
    """MACD main line = EMA(fast) - EMA(slow); None until both EMAs exist."""
    fast = ind_ema(values, fast_period)
    slow = ind_ema(values, slow_period)
    out = [None] * len(values)
    for i in range(len(values)):
        if fast[i] is not None and slow[i] is not None:
            out[i] = fast[i] - slow[i]
    return out'''

# Rolling standard deviation (population by default; Bessel-correct when sample).
STDDEV_SRC = '''def ind_stddev(values, period, sample=False):
    """Rolling stddev over ``period`` bars; population unless ``sample``."""
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        sd = math.sqrt(var)
        if sample and period > 1:
            sd *= math.sqrt(period / (period - 1.0))
        out[i] = sd
    return out'''

# Stochastic oscillator: raw %K then SMA-slowed %K and its %D signal.
STOCH_SRC = '''def ind_stochastic(highs, lows, closes, k_period, d_period, slowing):
    """Return (%K, %D) lists. %K is SMA-slowed; %D is the SMA of %K."""
    n = len(closes)
    raw_k = [None] * n
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        rng = hh - ll
        raw_k[i] = 50.0 if rng == 0.0 else (closes[i] - ll) / rng * 100.0
    k_line = _sma_skipnone(raw_k, slowing)
    d_line = _sma_skipnone(k_line, d_period)
    return k_line, d_line


def _sma_skipnone(values, period):
    """SMA over the last ``period`` non-None values; None until the window fills."""
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(len(values)):
        window = values[i - period + 1 : i + 1]
        if i - period + 1 >= 0 and all(v is not None for v in window):
            out[i] = sum(window) / period
    return out'''

# ZScore: rolling mean/stddev of the applied price, computed scalar Z per bar.
# Mirrors nodes/zscore.py: population sigma by default, Bessel when sample.
ZSCORE_SRC = '''def ind_zscore(values, window, sma_period, sample=False):
    """Return (z, mean, std, sma) aligned lists. z = (price - mean) / std."""
    mean = ind_sma(values, window)
    std = ind_stddev(values, window, sample=sample)
    sma = ind_sma(values, sma_period)
    z = [None] * len(values)
    for i in range(len(values)):
        if mean[i] is not None and std[i] is not None:
            z[i] = 0.0 if std[i] == 0.0 else (values[i] - mean[i]) / std[i]
    return z, mean, std, sma'''
