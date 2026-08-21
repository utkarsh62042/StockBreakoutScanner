"""Indicator sanity tests.

These don't try to match every chart platform bit-for-bit, but they catch
gross errors: wrong direction, NaN propagation, magnitude sanity, edge
cases at the start of the series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breakout.analysis.indicators import (
    add_standard_indicators,
    atr,
    bollinger_band_width,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    true_range,
    volume_ratio,
)


def _ohlcv(close: list[float], volume: list[float] | None = None) -> pd.DataFrame:
    """Build a DataFrame where high/low straddle close by ±0.5 — sufficient
    for indicator tests where exact OHLC structure doesn't matter."""
    close_arr = np.array(close, dtype=float)
    if volume is None:
        volume = [1000.0] * len(close)
    return pd.DataFrame(
        {
            "open": close_arr,
            "high": close_arr + 0.5,
            "low": close_arr - 0.5,
            "close": close_arr,
            "volume": volume,
        }
    )


# ── SMA / EMA ────────────────────────────────────────────────────────────


def test_sma_basic() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(s, 3)
    # First two values undefined (NaN), then rolling means
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)  # mean(1,2,3)
    assert result.iloc[3] == pytest.approx(3.0)  # mean(2,3,4)
    assert result.iloc[4] == pytest.approx(4.0)  # mean(3,4,5)


def test_sma_period_larger_than_series_returns_all_nan() -> None:
    s = pd.Series([1.0, 2.0, 3.0])
    result = sma(s, 10)
    assert result.isna().all()


def test_ema_tracks_input() -> None:
    """EMA converges to constant input."""
    s = pd.Series([5.0] * 50)
    result = ema(s, 10)
    assert result.iloc[-1] == pytest.approx(5.0)


def test_ema_faster_than_sma_to_react() -> None:
    """EMA weights recent values more — it should jump faster on a step input."""
    s = pd.Series([0.0] * 20 + [100.0] * 20)
    sma_result = sma(s, 10)
    ema_result = ema(s, 10)
    # Just after the step (e.g., 5 bars in), EMA should be closer to 100
    assert ema_result.iloc[25] > sma_result.iloc[25]


# ── True range / ATR ─────────────────────────────────────────────────────


def test_true_range_no_gap() -> None:
    """Without gaps, TR = high - low."""
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.0],
            "close": [11.0, 12.0],
            "volume": [1000, 1000],
        }
    )
    tr = true_range(df)
    # First bar: just high-low. Second bar: high(13)-prev_close(11)=2,
    # low(10)-prev_close(11)=1, high-low=3 → max is 3.
    assert tr.iloc[0] == pytest.approx(3.0)
    assert tr.iloc[1] == pytest.approx(3.0)


def test_true_range_with_gap_up() -> None:
    """A gap up makes TR larger than the bar's high-low range."""
    df = pd.DataFrame(
        {
            "open": [10.0, 20.0],
            "high": [11.0, 22.0],
            "low": [9.0, 19.0],
            "close": [10.5, 21.0],
            "volume": [1000, 1000],
        }
    )
    tr = true_range(df)
    # Second bar: high(22)-prev_close(10.5)=11.5, high-low=3, low-prev_close|=9.5
    # → max is 11.5
    assert tr.iloc[1] == pytest.approx(11.5)


def test_atr_is_positive_for_volatile_series() -> None:
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _ohlcv(list(closes))
    result = atr(df, 14)
    # After warmup, ATR should be defined and positive
    assert result.iloc[14:].notna().all()
    assert (result.iloc[14:] > 0).all()


def test_atr_warmup_period() -> None:
    """ATR is NaN for the first `period - 1` bars (Wilder needs warmup)."""
    df = _ohlcv(list(range(20)))
    result = atr(df, 14)
    assert pd.isna(result.iloc[12])
    # By index 14+ we should have values
    assert pd.notna(result.iloc[15])


# ── RSI ──────────────────────────────────────────────────────────────────


def test_rsi_in_bounds() -> None:
    """RSI must always sit in [0, 100]."""
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    result = rsi(close, 14)
    valid = result.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_rsi_only_gains_approaches_100() -> None:
    """Pure uptrend RSI should asymptote to 100."""
    close = pd.Series(np.arange(1.0, 100.0))  # 99 strictly increasing values
    result = rsi(close, 14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_only_losses_approaches_zero() -> None:
    close = pd.Series(np.arange(100.0, 1.0, -1.0))
    result = rsi(close, 14)
    assert result.iloc[-1] < 5  # very small, near zero


def test_rsi_flat_series_handled() -> None:
    """A flat series gives 0 gain and 0 loss — must not produce inf/nan after warmup."""
    close = pd.Series([50.0] * 50)
    result = rsi(close, 14)
    # avg_gain == avg_loss == 0; by our convention this returns 100
    # (the formula is undefined; either 50 or 100 is defensible).
    # We just require: no inf, no NaN after warmup.
    assert np.isfinite(result.iloc[-1])


# ── MACD ─────────────────────────────────────────────────────────────────


def test_macd_returns_three_series() -> None:
    close = pd.Series(np.arange(1.0, 100.0))
    line, signal, hist = macd(close)
    assert len(line) == len(close)
    assert len(signal) == len(close)
    assert len(hist) == len(close)


def test_macd_histogram_equals_line_minus_signal() -> None:
    close = pd.Series(np.cumsum(np.random.default_rng(0).normal(0, 1, 100)) + 100)
    line, signal, hist = macd(close)
    pd.testing.assert_series_equal(hist, line - signal, check_names=False)


# ── Bollinger Bands ──────────────────────────────────────────────────────


def test_bollinger_upper_above_middle_above_lower() -> None:
    close = pd.Series(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 100)))
    upper, middle, lower = bollinger_bands(close, 20, 2.0)
    valid = upper.notna() & middle.notna() & lower.notna()
    assert (upper[valid] >= middle[valid]).all()
    assert (middle[valid] >= lower[valid]).all()


def test_bollinger_width_decreases_when_volatility_falls() -> None:
    """A high-vol then low-vol series should show width contracting."""
    rng = np.random.default_rng(0)
    high_vol = rng.normal(0, 5, 50)
    low_vol = rng.normal(0, 0.1, 50)
    close = pd.Series(100 + np.cumsum(np.concatenate([high_vol, low_vol])))
    width = bollinger_band_width(close, 20)
    # End width should be much smaller than middle of high-vol section
    assert width.iloc[-1] < width.iloc[45]


# ── Volume ratio ─────────────────────────────────────────────────────────


def test_volume_ratio_one_for_flat_volume() -> None:
    volume = pd.Series([1000.0] * 50)
    result = volume_ratio(volume, 20)
    assert result.iloc[-1] == pytest.approx(1.0)


def test_volume_ratio_spike_detected() -> None:
    """A 3x volume bar should show ratio ≈ 3."""
    volume = pd.Series([1000.0] * 30 + [3000.0])
    result = volume_ratio(volume, 20)
    # The last value: 3000 / avg(last 20 of which 19 are 1000 and 1 is 3000)
    # = 3000 / ((19*1000 + 3000)/20) = 3000 / 1100 ≈ 2.727
    assert result.iloc[-1] == pytest.approx(2.727, rel=0.01)


# ── add_standard_indicators ──────────────────────────────────────────────


def test_add_standard_indicators_attaches_expected_columns() -> None:
    df = _ohlcv(list(np.arange(1.0, 250.0)))
    out = add_standard_indicators(df)
    expected = {
        "sma_50",
        "sma_150",
        "sma_200",
        "atr_14",
        "atr_10",
        "atr_50",
        "vol_avg_20",
        "volume_ratio_20",
    }
    assert expected.issubset(out.columns)
    # Last row should have all indicators defined (we passed enough warmup)
    last = out.iloc[-1]
    for col in expected:
        assert pd.notna(last[col]), f"{col} is NaN at last row"


def test_add_standard_indicators_does_not_mutate_input() -> None:
    df = _ohlcv(list(range(50)))
    original_cols = set(df.columns)
    _ = add_standard_indicators(df)
    assert set(df.columns) == original_cols  # input unchanged
