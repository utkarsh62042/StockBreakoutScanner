"""Tightness (volatility contraction) tests."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from breakout.analysis.tightness import tightness_points, tightness_score


def _ranges_df(ranges: list[float], price: float = 100.0) -> pd.DataFrame:
    """OHLCV with a constant close and a per-bar high/low spread of `ranges`,
    so each bar's true range equals its spread (no gaps)."""
    n = len(ranges)
    highs = [price + r / 2 for r in ranges]
    lows = [price - r / 2 for r in ranges]
    return pd.DataFrame(
        {
            "open": [price] * n,
            "high": highs,
            "low": lows,
            "close": [price] * n,
            "volume": [100000.0] * n,
        }
    )


def test_contracting_series_scores_positive() -> None:
    # 40 wide bars (range 10) then 20 tight bars (range 1) → strong contraction.
    df = _ranges_df([10.0] * 40 + [1.0] * 20)
    score = tightness_score(df)
    assert score > 0.3
    assert tightness_points(score) > 3.0


def test_expanding_series_scores_negative_and_zero_points() -> None:
    df = _ranges_df([1.0] * 40 + [10.0] * 20)
    score = tightness_score(df)
    assert score < 0.0
    assert tightness_points(score) == 0.0


def test_constant_volatility_scores_near_zero() -> None:
    df = _ranges_df([5.0] * 60)
    score = tightness_score(df)
    assert score == pytest.approx(0.0, abs=1e-9)
    assert tightness_points(score) == pytest.approx(0.0)


def test_insufficient_history_returns_zero() -> None:
    df = _ranges_df([5.0] * 30)   # < long + 1 (default long = 50)
    assert tightness_score(df) == 0.0


def test_tightness_points_clamped_to_range() -> None:
    assert tightness_points(0.5) == pytest.approx(5.0)
    assert tightness_points(2.0) == pytest.approx(10.0)     # capped
    assert tightness_points(-0.3) == 0.0                    # expansion → 0
    assert tightness_points(math.nan) == 0.0


def test_tightness_points_custom_cap() -> None:
    assert tightness_points(0.8, max_points=20.0) == pytest.approx(16.0)
