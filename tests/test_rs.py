"""Relative-strength tests."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from breakout.analysis.rs import (
    period_return,
    relative_strength,
    rs_percentile_ranks,
    rs_points,
)


def _close_df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [100000.0] * n,
        }
    )


# ── period_return ─────────────────────────────────────────────────────────


def test_period_return_basic() -> None:
    # 64 bars: close 63 bars ago = 100, today = 110 → +10%.
    closes = [100.0] + [0.0] * 62 + [110.0]
    closes[1:63] = list(np.linspace(100, 110, 62))
    df = _close_df(closes)
    assert period_return(df, lookback=63) == pytest.approx(0.10)


def test_period_return_insufficient_history_is_nan() -> None:
    df = _close_df([100.0] * 40)
    assert math.isnan(period_return(df, lookback=63))


# ── relative_strength ─────────────────────────────────────────────────────


def test_relative_strength_outperform_gt_one() -> None:
    assert relative_strength(0.20, 0.05) > 1.0


def test_relative_strength_underperform_lt_one() -> None:
    assert relative_strength(0.02, 0.10) < 1.0


def test_relative_strength_zero_benchmark_is_finite() -> None:
    # Literal return-ratio would divide by zero; growth-factor form is safe.
    assert relative_strength(0.10, 0.0) == pytest.approx(1.10)


# ── rs_percentile_ranks ───────────────────────────────────────────────────


def test_percentile_ranks_order_best_to_worst() -> None:
    ranks = rs_percentile_ranks({"A": 0.30, "B": 0.20, "C": 0.10, "D": 0.0, "E": -0.10})
    assert ranks["A"] == pytest.approx(100.0)
    assert ranks["E"] == pytest.approx(0.0)
    assert ranks["C"] == pytest.approx(50.0)
    assert ranks["A"] > ranks["B"] > ranks["C"] > ranks["D"] > ranks["E"]


def test_percentile_ranks_ties_share_rank() -> None:
    ranks = rs_percentile_ranks({"A": 0.10, "B": 0.10, "C": 0.30, "D": -0.10})
    assert ranks["A"] == pytest.approx(ranks["B"])


def test_percentile_ranks_drops_nan() -> None:
    ranks = rs_percentile_ranks({"A": 0.10, "B": math.nan, "C": -0.10})
    assert "B" not in ranks
    assert set(ranks) == {"A", "C"}


def test_percentile_ranks_single_symbol_neutral() -> None:
    assert rs_percentile_ranks({"A": 0.10}) == {"A": 50.0}


def test_percentile_ranks_empty() -> None:
    assert rs_percentile_ranks({}) == {}


# ── rs_points ─────────────────────────────────────────────────────────────


def test_rs_points_full_at_top_quartile() -> None:
    assert rs_points(75.0) == pytest.approx(15.0)
    assert rs_points(90.0) == pytest.approx(15.0)
    assert rs_points(100.0) == pytest.approx(15.0)


def test_rs_points_zero_at_or_below_median() -> None:
    assert rs_points(50.0) == 0.0
    assert rs_points(40.0) == 0.0


def test_rs_points_linear_between() -> None:
    assert rs_points(62.5) == pytest.approx(7.5)     # midpoint of 50..75
    assert rs_points(60.0) == pytest.approx((60 - 50) / 25 * 15)


def test_rs_points_nan_is_zero() -> None:
    assert rs_points(math.nan) == 0.0
