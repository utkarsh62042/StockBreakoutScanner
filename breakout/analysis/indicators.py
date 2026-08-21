"""Technical indicators used across the scanner.

All indicators are pure functions: they take a Series or DataFrame and return
a new Series (or tuple of Series) without mutating inputs. Column names are
expected lowercase (`high`, `low`, `close`, `volume`) — the data layer
normalizes them before they reach this module.

Smoothing convention: Wilder's smoothing (used by ATR, RSI, ADX) is implemented
via pandas' EMA with alpha=1/period and adjust=False, matching the convention
used by TradingView and most charting platforms.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Moving averages ──────────────────────────────────────────────────────


def sma(s: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return s.rolling(window=period, min_periods=period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard, not Wilder)."""
    return s.ewm(span=period, adjust=False).mean()


# ── True range / ATR ─────────────────────────────────────────────────────


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high-low, |high - prev_close|, |low - prev_close|).

    The first bar has no prev_close, so its TR is just high - low.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing.

    Wilder = EMA with alpha = 1/period (equivalent to span = 2*period - 1).
    """
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ── RSI ──────────────────────────────────────────────────────────────────


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index, Wilder smoothing.

    Returns values in [0, 100]. Conventionally:
        >70 = overbought, <30 = oversold (rule of thumb; tune per stock).
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # When avg_loss is 0 the formula is undefined; treat as 100 (pure gains).
    result = result.where(avg_loss != 0, 100.0)
    return result


# ── MACD ─────────────────────────────────────────────────────────────────


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── Bollinger Bands ──────────────────────────────────────────────────────


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower) bands."""
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def bollinger_band_width(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> pd.Series:
    """BB width normalized by middle band — comparable across price levels.

    Used by the BB squeeze pattern: a low value means contracted volatility.
    """
    upper, middle, lower = bollinger_bands(close, period, num_std)
    return (upper - lower) / middle


# ── Volume ratio ─────────────────────────────────────────────────────────


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Today's volume divided by the rolling N-day average.

    Used for the breakout confirmation: a breakout candle should print
    significantly above its 20-day average (default threshold: 1.5x).
    """
    vol_avg = volume.rolling(window=period, min_periods=period).mean()
    return volume / vol_avg


# ── ADX ──────────────────────────────────────────────────────────────────


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength regardless of direction.

    >25 = strong trend; <20 = no trend / ranging. Wilder smoothing throughout.
    """
    high = df["high"]
    low = df["low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move

    tr = true_range(df)
    atr_series = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_series
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_series

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ── Composite helper ─────────────────────────────────────────────────────


def add_standard_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the indicators used by Phase 1 scoring + stage classification.

    Returns a copy with new columns:
        sma_50, sma_150, sma_200, atr_14, atr_10, atr_50, vol_avg_20,
        volume_ratio_20.
    The 150-day SMA approximates the 30-week SMA used in Weinstein staging.
    """
    out = df.copy()
    out["sma_50"] = sma(out["close"], 50)
    out["sma_150"] = sma(out["close"], 150)  # ≈ 30-week
    out["sma_200"] = sma(out["close"], 200)
    out["atr_14"] = atr(out, 14)
    out["atr_10"] = atr(out, 10)
    out["atr_50"] = atr(out, 50)
    out["vol_avg_20"] = out["volume"].rolling(window=20, min_periods=20).mean()
    out["volume_ratio_20"] = out["volume"] / out["vol_avg_20"]
    return out
