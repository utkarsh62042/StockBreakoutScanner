"""Failed-breakout detection (eod_settle._is_failed_breakout)."""

from __future__ import annotations

from datetime import date

from breakout.jobs.eod_settle import _is_failed_breakout

LEVEL = 100.0
BD = "2026-08-03"


def test_close_below_level_within_window_is_failed() -> None:
    assert _is_failed_breakout(BD, LEVEL, date(2026, 8, 5), 98.0) is True   # 2 days later


def test_close_above_level_not_failed() -> None:
    assert _is_failed_breakout(BD, LEVEL, date(2026, 8, 5), 101.0) is False


def test_beyond_window_not_failed() -> None:
    assert _is_failed_breakout(BD, LEVEL, date(2026, 8, 10), 90.0) is False  # 7 days later


def test_missing_data_not_failed() -> None:
    assert _is_failed_breakout(BD, None, date(2026, 8, 5), 98.0) is False
    assert _is_failed_breakout(BD, LEVEL, date(2026, 8, 5), None) is False
    assert _is_failed_breakout("", LEVEL, date(2026, 8, 5), 98.0) is False


def test_bad_date_not_failed() -> None:
    assert _is_failed_breakout("not-a-date", LEVEL, date(2026, 8, 5), 98.0) is False
