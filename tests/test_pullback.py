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
    # Down-close bar, but a long lower wick shows the retest was bought.
    assert _is_pullback_entry(_bar(o=101.0, h=101.5, low=100.0, c=100.6), LEVEL) is True


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
