"""Pullback-entry retest logic (preclose_scan._is_pullback_entry)."""

from __future__ import annotations

import pandas as pd

from breakout.jobs.preclose_scan import _is_pullback_entry


def _bar(o: float, h: float, low: float, c: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [o, o], "high": [h, h], "low": [low, low], "close": [c, c], "volume": [1.0, 1.0]}
    )


LEVEL = 100.0


def test_retest_up_close_is_entry() -> None:
    # Low dips to just above the level; closes up and above it.
    assert _is_pullback_entry(_bar(o=100.8, h=102.5, low=100.5, c=102.0), LEVEL) is True


def test_retest_long_lower_wick_is_entry() -> None:
    # Down-close bar with long lower wick, close in upper 50% of range shows retest was bought.
    # Range = 1.5, close at 100.75 = 50% of range (exactly meets threshold)
    assert _is_pullback_entry(_bar(o=101.0, h=101.5, low=100.0, c=100.75), LEVEL) is True


def test_never_reached_level_not_entry() -> None:
    # Low stayed well above the level — no retest.
    assert _is_pullback_entry(_bar(o=103.5, h=104.0, low=103.0, c=103.8), LEVEL) is False


def test_broke_below_level_not_entry() -> None:
    # Support failed (low well below the level).
    assert _is_pullback_entry(_bar(o=99.5, h=100.2, low=98.0, c=99.8), LEVEL) is False


def test_no_reversal_not_entry() -> None:
    # Touched and closed above, but a big down bar with no lower wick.
    assert _is_pullback_entry(_bar(o=103.0, h=103.0, low=100.5, c=100.5), LEVEL) is False


def test_close_below_level_not_entry() -> None:
    assert _is_pullback_entry(_bar(o=101.0, h=101.5, low=100.5, c=99.0), LEVEL) is False


def test_retest_1pct_above_level_is_entry() -> None:
    # Close exactly 1% above level (meets absolute distance threshold).
    # Even with weak close position, the 1% distance rule applies.
    assert _is_pullback_entry(_bar(o=100.5, h=101.5, low=100.0, c=101.0), LEVEL) is True


def test_retest_weak_close_rejected() -> None:
    # Close 0.5% above level with only 40% of range (both fail thresholds).
    # Range = 1.5, close at 100.5 = 33% of range (needs 50%)
    # 0.5% above level (needs 1%)
    assert _is_pullback_entry(_bar(o=101.0, h=101.5, low=100.0, c=100.5), LEVEL) is False


def test_tight_consolidation_held() -> None:
    # Tight consolidation with strong close position validates institutional support.
    # Range = 0.10, close at 100.08 = 80% of range (well above 50%)
    assert _is_pullback_entry(_bar(o=100.05, h=100.10, low=100.0, c=100.08), 100.0) is True


def test_low_price_stock_1pct_threshold() -> None:
    # Low-priced stocks: close 1% above level (₹10 → ₹10.10) passes.
    assert _is_pullback_entry(_bar(o=10.05, h=10.15, low=10.0, c=10.10), 10.0) is True


def test_high_price_stock_1pct_threshold() -> None:
    # High-priced stocks: close 1% above level (₹500 → ₹505) passes.
    assert _is_pullback_entry(_bar(o=502.5, h=507.5, low=500.0, c=505.0), 500.0) is True
