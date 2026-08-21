"""Pivot detection tests.

These fixtures are hand-checked. If a fixture changes, recompute the expected
pivots by hand — do not regenerate from the code under test.

Every chart pattern depends on pivot correctness, so this suite is the most
important guard against silent regressions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breakout.analysis.pivots import (
    Pivot,
    PivotKind,
    Pivots,
    find_pivots,
)


def _make_df(highs: list[float], lows: list[float] | None = None) -> pd.DataFrame:
    """Build an OHLCV-shaped DataFrame for pivot tests.

    If `lows` is omitted, lows mirror highs (high - 1). Only `high` and `low`
    matter for pivot detection; other columns are filled with dummy values.
    """
    if lows is None:
        lows = [h - 1 for h in highs]
    assert len(highs) == len(lows)
    n = len(highs)
    return pd.DataFrame(
        {
            "open": highs,
            "high": highs,
            "low": lows,
            "close": highs,
            "volume": [1000] * n,
        }
    )


# ── Smoke tests ──────────────────────────────────────────────────────────


def test_empty_dataframe_returns_empty_pivots() -> None:
    df = _make_df([])
    result = find_pivots(df, n=5)
    assert result.highs == []
    assert result.lows == []


def test_dataframe_smaller_than_window_returns_empty() -> None:
    # n=5 needs at least 11 bars (5 left + pivot + 5 right). 10 bars => empty.
    df = _make_df(list(range(10)))
    result = find_pivots(df, n=5)
    assert result.highs == []
    assert result.lows == []


def test_negative_n_raises() -> None:
    df = _make_df(list(range(20)))
    with pytest.raises(ValueError):
        find_pivots(df, n=0)
    with pytest.raises(ValueError):
        find_pivots(df, n=-1)


def test_missing_columns_raises() -> None:
    df = pd.DataFrame({"close": [1, 2, 3]})
    with pytest.raises(ValueError, match="missing required columns"):
        find_pivots(df, n=2)


# ── Simple geometric fixtures ────────────────────────────────────────────


def test_M_shape_detects_two_highs_and_one_low() -> None:
    """An 'M' shape: ascent, descent, ascent, descent."""
    # Indices:            0   1   2   3   4   5   6   7   8   9  10
    highs = [10, 12, 14, 16, 14, 12, 14, 16, 14, 12, 10]
    lows = [8, 9, 10, 12, 10, 8, 10, 12, 10, 9, 8]
    df = _make_df(highs, lows)
    result = find_pivots(df, n=2)
    assert result.high_indices() == [3, 7]
    assert result.low_indices() == [5]


def test_V_shape_detects_one_low() -> None:
    """A V shape: monotonic down, then monotonic up."""
    highs = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11]
    lows = [9, 8, 7, 6, 5, 6, 7, 8, 9, 10]
    df = _make_df(highs, lows)
    result = find_pivots(df, n=2)
    assert result.high_indices() == []
    assert result.low_indices() == [4]


def test_inverted_V_detects_one_high() -> None:
    """Inverted V: monotonic up, then monotonic down."""
    highs = [1, 2, 3, 4, 5, 4, 3, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.high_indices() == [4]
    assert result.low_indices() == []


def test_monotonic_up_detects_nothing() -> None:
    df = _make_df(list(range(1, 21)))  # 1, 2, 3, ..., 20
    result = find_pivots(df, n=3)
    assert result.high_indices() == []
    assert result.low_indices() == []


def test_monotonic_down_detects_nothing() -> None:
    df = _make_df(list(range(20, 0, -1)))  # 20, 19, ..., 1
    result = find_pivots(df, n=3)
    assert result.high_indices() == []
    assert result.low_indices() == []


# ── Strictness / flat-top rejection ──────────────────────────────────────


def test_flat_top_two_bars_rejected() -> None:
    """A flat top with two bars at the same high should produce no pivot.

    This is the core strictness guarantee: ambiguous tops are noise we'd
    rather skip than feed into pattern detection.
    """
    # Two bars at high=13 (indices 3 and 4) — neither is a strict pivot
    highs = [10, 11, 12, 13, 13, 12, 11, 10, 9]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.high_indices() == []


def test_flat_top_three_bars_rejected() -> None:
    highs = [10, 11, 12, 13, 13, 13, 12, 11, 10]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.high_indices() == []


def test_flat_bottom_rejected() -> None:
    """Symmetric: flat bottoms also rejected."""
    highs = [10, 9, 8, 7, 7, 8, 9, 10, 11]
    lows = [9, 8, 7, 6, 6, 7, 8, 9, 10]
    df = _make_df(highs, lows)
    result = find_pivots(df, n=2)
    assert result.low_indices() == []


def test_close_but_not_equal_is_accepted() -> None:
    """A high that's only 0.01 above its neighbors still qualifies."""
    highs = [10, 11, 12, 13.01, 13.00, 12, 11, 10, 9]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.high_indices() == [3]


# ── Edge bars never confirmed ────────────────────────────────────────────


def test_first_n_bars_never_pivots_even_if_extreme() -> None:
    """Even an obvious extreme in the first N bars cannot be a pivot."""
    highs = [100.0, 50, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6, 50.7]
    # The 100 at index 0 is the obvious high but has no left context
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert 0 not in result.high_indices()


def test_last_n_bars_never_pivots_even_if_extreme() -> None:
    """An extreme in the last N bars also cannot be confirmed yet."""
    highs = [50, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6, 50.7, 100.0]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    # Index 8 (the 100) needs 2 future bars to confirm — none exist
    assert 8 not in result.high_indices()


def test_pivot_at_index_n_is_allowed() -> None:
    """The earliest index that can be a confirmed pivot is exactly N."""
    # Want pivot at index 2 with n=2 — need values strictly less at idx 0,1,3,4
    highs = [1, 2, 5, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.high_indices() == [2]


def test_pivot_at_index_len_minus_n_minus_one_is_allowed() -> None:
    """The latest index that can be confirmed is len - n - 1."""
    # 7 bars, n=2 -> last possible pivot at index 4
    highs = [1, 2, 1, 2, 5, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert 4 in result.high_indices()


# ── Strength computation ────────────────────────────────────────────────


def test_strength_balanced_pivot() -> None:
    """A pivot equally dominant on both sides has strength = run length."""
    # Pivot at index 5, dominant 5 bars left and 5 bars right
    highs = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert len(result.highs) == 1
    p = result.highs[0]
    assert p.index == 5
    assert p.strength == 5  # min(5, 5)


def test_strength_asymmetric_pivot() -> None:
    """Strength is the smaller of left/right run — conservative measure."""
    # Pivot at index 9 dominates 9 bars left, 3 bars right
    highs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert len(result.highs) == 1
    p = result.highs[0]
    assert p.index == 9
    assert p.strength == 3  # min(9, 3)


def test_strength_for_low_pivot() -> None:
    """Strength logic mirrors for low pivots — counts bars strictly above."""
    highs = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10]
    lows = [9, 8, 7, 6, 5, 1, 5, 6, 7, 8, 9]
    df = _make_df(highs, lows)
    result = find_pivots(df, n=2)
    assert len(result.lows) == 1
    p = result.lows[0]
    assert p.index == 5
    # Left: 5 bars above 1. Right: 5 bars above 1.
    assert p.strength == 5


# ── Pivot object correctness ────────────────────────────────────────────


def test_pivot_returns_correct_price() -> None:
    highs = [1, 2, 3, 17.5, 3, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert result.highs[0].price == pytest.approx(17.5)


def test_pivot_returns_correct_kind() -> None:
    highs = [1, 2, 3, 5, 3, 2, 1]
    lows = [0, 1, 2, 4, 2, 1, 0]
    df = _make_df(highs, lows)
    result = find_pivots(df, n=2)
    # Index 3 is both swing high (5) and swing low (4) in this fixture
    assert result.highs[0].kind == PivotKind.HIGH
    assert all(p.kind == PivotKind.HIGH for p in result.highs)
    assert all(p.kind == PivotKind.LOW for p in result.lows)


def test_pivots_returned_in_chronological_order() -> None:
    """The lists must be sorted by index ascending — pattern detectors assume this."""
    # Multiple highs at known positions
    highs = [1, 5, 1, 5, 1, 5, 1, 5, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=1)
    indices = result.high_indices()
    assert indices == sorted(indices), "pivot indices must be ascending"


# ── Parameter sensitivity ────────────────────────────────────────────────


def test_larger_n_detects_fewer_pivots() -> None:
    """Increasing N filters out minor swings, keeping only major pivots."""
    # Noisy series with several small highs and one big one
    rng = np.random.default_rng(seed=42)
    base = np.concatenate([
        rng.normal(loc=100, scale=2, size=20),
        [110, 115, 120, 115, 110],  # clear major high around index 22
        rng.normal(loc=100, scale=2, size=20),
    ])
    df = _make_df(list(base.astype(float)))
    pivots_n2 = find_pivots(df, n=2)
    pivots_n5 = find_pivots(df, n=5)
    assert len(pivots_n5.highs) <= len(pivots_n2.highs)
    # The major high at index 22 should survive at any reasonable N
    assert 22 in pivots_n2.high_indices()
    assert 22 in pivots_n5.high_indices()


def test_n_equals_1_detects_local_extrema_only() -> None:
    """n=1 means any bar higher than its two neighbors is a swing high."""
    highs = [1, 3, 2, 4, 2, 5, 2]
    df = _make_df(highs)
    result = find_pivots(df, n=1)
    assert result.high_indices() == [1, 3, 5]


# ── Realistic-shape sanity ───────────────────────────────────────────────


def test_uptrend_with_higher_highs_and_higher_lows() -> None:
    """A clean uptrend should produce pivots forming higher-highs / higher-lows."""
    # Three clear swings up
    highs = [
        10, 11, 12, 15, 12, 11,        # high pivot @ 3 (=15)
        13, 14, 18, 14, 13,            # high pivot @ 8 (=18)
        15, 16, 21, 16, 15, 14         # high pivot @ 13 (=21)
    ]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    high_idx = result.high_indices()
    # Verify higher-highs structure
    high_prices = [result.highs[i].price for i in range(len(result.highs))]
    assert high_idx == [3, 8, 13]
    assert high_prices == sorted(high_prices), "uptrend should have ascending pivot highs"


def test_pivots_container_helpers() -> None:
    highs = [1, 2, 3, 5, 3, 2, 1, 2, 4, 2, 1]
    df = _make_df(highs)
    result = find_pivots(df, n=2)
    assert isinstance(result, Pivots)
    last_two = result.last_n_highs(2)
    assert len(last_two) == min(2, len(result.highs))
    # last_n returns the *most recent* — i.e. last in chronological order
    if len(result.highs) >= 2:
        assert last_two[-1] == result.highs[-1]
