"""Stale bars must not produce signals.

The morning scan decided which symbols to fetch *before* fetching them, and then
scanned everything in the universe regardless of whether its fetch succeeded. A
symbol whose fetch failed kept whatever was in the cache and was analysed
against it — so a breakout "signal" could be generated from prices days old.

The subtlety is what "fresh" means. Before 3:30 PM today's bar is still being
written and is deliberately excluded (see `analysis/session.py`), so "is the
latest bar today?" is the wrong test for the 9:30 scan — it would reject the
entire universe. The yardstick is the last session that actually *closed*.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from breakout.analysis.session import IST
from breakout.jobs.preclose_scan import _has_todays_bar
from breakout.trading_calendar import (
    last_completed_session,
    previous_trading_day,
)

# 2026-09-07 is a Monday, so that week runs Mon-Fri 07-11.
MON = date(2026, 9, 7)
TUE = date(2026, 9, 8)
FRI = date(2026, 9, 11)
SAT = date(2026, 9, 12)
NO_HOLIDAYS: set[date] = set()


def _at(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


# ── previous_trading_day ─────────────────────────────────────────────────


def test_previous_trading_day_steps_back_one_weekday() -> None:
    assert previous_trading_day(TUE, NO_HOLIDAYS) == MON


def test_previous_trading_day_skips_the_weekend() -> None:
    assert previous_trading_day(MON, NO_HOLIDAYS) == date(2026, 9, 4)


def test_previous_trading_day_skips_holidays() -> None:
    assert previous_trading_day(TUE, {MON}) == date(2026, 9, 4)


def test_previous_trading_day_survives_a_long_closure() -> None:
    """A bad holidays file must not spin forever."""
    everything = {date(2026, 9, d) for d in range(1, 8)}
    assert previous_trading_day(TUE, everything) < TUE


# ── last_completed_session — the yardstick for freshness ─────────────────


def test_before_the_close_the_last_completed_session_is_yesterday() -> None:
    """The 9:30 scan's case. Today's bar is still being written."""
    assert last_completed_session(_at(TUE, 9, 30), NO_HOLIDAYS) == MON


def test_at_three_pm_it_is_still_yesterday() -> None:
    assert last_completed_session(_at(TUE, 15, 0), NO_HOLIDAYS) == MON


def test_after_the_close_today_counts() -> None:
    assert last_completed_session(_at(TUE, 15, 30), NO_HOLIDAYS) == TUE
    assert last_completed_session(_at(TUE, 18, 0), NO_HOLIDAYS) == TUE


def test_on_a_weekend_it_is_the_last_weekday() -> None:
    assert last_completed_session(_at(SAT, 10, 0), NO_HOLIDAYS) == FRI


def test_on_a_holiday_it_is_the_previous_session() -> None:
    assert last_completed_session(_at(TUE, 18, 0), {TUE}) == MON


# ── The freshness comparison the morning scan makes ──────────────────────


def test_a_symbol_frozen_days_ago_is_not_fresh() -> None:
    required = last_completed_session(_at(TUE, 9, 30), NO_HOLIDAYS)
    assert not (date(2026, 9, 2) >= required)


def test_a_symbol_updated_through_yesterday_is_fresh_at_930() -> None:
    """The normal case — this must NOT be excluded, or the scan finds nothing."""
    required = last_completed_session(_at(TUE, 9, 30), NO_HOLIDAYS)
    assert MON >= required


def test_a_symbol_already_carrying_todays_partial_bar_is_fresh() -> None:
    required = last_completed_session(_at(TUE, 9, 30), NO_HOLIDAYS)
    assert TUE >= required


# ── The pre-close scan asks a different question ─────────────────────────


def _frame(days: list[date]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="date")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=idx,
    )


def test_preclose_requires_todays_bar() -> None:
    """It is asking "did this break out *today*" — yesterday's close cannot
    answer that, and confirming against it would open a position on a move that
    may already be over."""
    assert _has_todays_bar(_frame([MON, TUE]), _at(TUE, 15, 0)) is True
    assert _has_todays_bar(_frame([MON]), _at(TUE, 15, 0)) is False


def test_preclose_freshness_is_measured_in_ist() -> None:
    """A scheduler in another timezone must not mislabel yesterday as today."""
    import zoneinfo

    utc_930 = datetime(2026, 9, 8, 9, 30, tzinfo=zoneinfo.ZoneInfo("UTC"))
    assert _has_todays_bar(_frame([MON, TUE]), utc_930.astimezone(IST)) is True


def test_preclose_handles_an_empty_frame() -> None:
    assert _has_todays_bar(_frame([]), _at(TUE, 15, 0)) is False
