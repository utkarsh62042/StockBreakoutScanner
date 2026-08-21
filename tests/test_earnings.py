"""Earnings-blackout filter tests."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from breakout.filters.earnings import in_earnings_blackout, trading_days_between


def _bday(d: date, n: int = 0) -> date:
    """A business day `n` trading days from `d` (rolls d forward if needed)."""
    return pd.Timestamp(np.busday_offset(np.datetime64(d, "D"), n, roll="forward")).date()


def test_trading_days_between_is_order_independent() -> None:
    a, b = _bday(date(2026, 2, 9)), _bday(date(2026, 2, 9), 4)
    assert trading_days_between(a, b) == 4
    assert trading_days_between(b, a) == 4


def test_future_earnings_within_window_blackout() -> None:
    today = _bday(date(2026, 2, 9))          # February = non-season
    assert in_earnings_blackout(today, [_bday(today, 3)]) is True


def test_earnings_far_away_not_blackout() -> None:
    today = _bday(date(2026, 2, 9))
    assert in_earnings_blackout(today, [_bday(today, 15)]) is False


def test_post_earnings_allowed_by_default() -> None:
    today = _bday(date(2026, 2, 9))
    past = _bday(today, -3)
    assert in_earnings_blackout(today, [past]) is False
    assert in_earnings_blackout(today, [past], allow_post_earnings=False) is True


def test_season_window_is_wider() -> None:
    # 6 trading days out: outside the normal 5-day window, inside the 7-day
    # season window.
    apr = _bday(date(2026, 4, 6))            # April = earnings season
    assert in_earnings_blackout(apr, [_bday(apr, 6)]) is True
    feb = _bday(date(2026, 2, 9))            # same gap, non-season → allowed
    assert in_earnings_blackout(feb, [_bday(feb, 6)]) is False


def test_empty_and_none_dates_not_blackout() -> None:
    today = _bday(date(2026, 2, 9))
    assert in_earnings_blackout(today, []) is False
    assert in_earnings_blackout(today, None) is False
    assert in_earnings_blackout(today, [None]) is False
