"""Partial-session volume against full-day averages.

Both scans compare today's volume to a 20-day mean of *complete* days. While
today's bar is still being written that comparison is biased in one direction
only — the partial bar is always too small — so the 1.5x gate is harder to clear
than configured and the volume score reads low. Now that the volume component
actually discriminates, the bias matters.

The naive correction (scale by elapsed clock time) is wrong: NSE volume is
U-shaped, so clock time overstates progress all session long.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from breakout.analysis.session import (
    IST,
    MIN_PROJECTABLE_FRACTION,
    drop_partial_bar,
    is_partial_bar,
    minutes_into_session,
    project_full_day_volume,
    session_volume_fraction,
)
from breakout.jobs.preclose_scan import _volume_ratio

TODAY = date(2026, 9, 10)


def _at(hh: int, mm: int, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


# ── The measured profile ─────────────────────────────────────────────────


def test_three_pm_is_about_85_percent_not_92() -> None:
    """The headline measurement. Clock time says 345/375 = 92%; 276 symbol-days
    of 5-minute bars say 85%, because the closing half hour is heavy."""
    frac = session_volume_fraction(_at(15, 0))
    assert frac == pytest.approx(0.851, abs=0.01)
    assert frac < 345 / 375


def test_clock_time_overstates_progress_through_the_session() -> None:
    for hh, mm in ((11, 30), (12, 30), (13, 30), (14, 30)):
        elapsed = minutes_into_session(_at(hh, mm)) / 375.0
        assert session_volume_fraction(_at(hh, mm)) < elapsed


def test_the_profile_is_monotonic() -> None:
    marks = [_at(h, m) for h, m in
             ((9, 15), (9, 30), (10, 30), (11, 30), (12, 30), (13, 30),
              (14, 30), (15, 0), (15, 30))]
    fracs = [session_volume_fraction(m) for m in marks]
    assert fracs == sorted(fracs)


def test_the_session_boundaries_are_zero_and_one() -> None:
    assert session_volume_fraction(_at(9, 15)) == pytest.approx(0.0)
    assert session_volume_fraction(_at(15, 30)) == pytest.approx(1.0)


def test_outside_the_session_is_clamped() -> None:
    assert session_volume_fraction(_at(6, 0)) == pytest.approx(0.0)
    assert session_volume_fraction(_at(23, 0)) == pytest.approx(1.0)


def test_the_fraction_does_not_depend_on_machine_timezone() -> None:
    """A naive datetime is read as IST, and an aware one is converted — the
    scheduler's host timezone must not change the answer."""
    import zoneinfo

    naive = datetime(2026, 9, 10, 15, 0)
    utc = datetime(2026, 9, 10, 9, 30, tzinfo=zoneinfo.ZoneInfo("UTC"))  # 15:00 IST
    assert session_volume_fraction(naive) == pytest.approx(
        session_volume_fraction(utc)
    )


# ── Projection ───────────────────────────────────────────────────────────


def test_projection_scales_a_three_pm_bar_up() -> None:
    projected, frac = project_full_day_volume(851_000, _at(15, 0))
    assert frac == pytest.approx(0.851, abs=0.01)
    assert projected == pytest.approx(1_000_000, rel=0.02)


def test_a_genuine_1point5x_day_clears_the_gate_at_3pm() -> None:
    """The concrete failure this fixes: a true 1.5x day showed as ~1.27x and was
    rejected by the 1.5x confirmation gate."""
    avg = 1_000_000.0
    observed_at_3pm = 1.5 * avg * 0.851
    assert observed_at_3pm / avg < 1.5              # what it used to read
    projected, _ = project_full_day_volume(observed_at_3pm, _at(15, 0))
    assert projected / avg == pytest.approx(1.5, rel=0.01)


def test_early_session_is_not_projected() -> None:
    """Dividing a 15-minute bar by 0.04 multiplies noise by ~26. Below the
    threshold the volume is returned untouched so callers can tell."""
    vol, frac = project_full_day_volume(50_000, _at(9, 30))
    assert vol == 50_000
    assert frac < MIN_PROJECTABLE_FRACTION


def test_after_the_close_projection_is_a_no_op() -> None:
    vol, frac = project_full_day_volume(1_000_000, _at(16, 0))
    assert vol == pytest.approx(1_000_000) and frac == pytest.approx(1.0)


# ── Deciding whether a bar is partial ────────────────────────────────────


def test_todays_bar_before_the_close_is_partial() -> None:
    assert is_partial_bar(TODAY, _at(15, 0)) is True


def test_todays_bar_after_the_close_is_settled() -> None:
    assert is_partial_bar(TODAY, _at(15, 30)) is False
    assert is_partial_bar(TODAY, _at(16, 0)) is False


def test_an_earlier_day_is_never_partial() -> None:
    assert is_partial_bar(date(2026, 9, 9), _at(11, 0)) is False


def test_missing_date_is_not_partial() -> None:
    assert is_partial_bar(None, _at(11, 0)) is False


# ── Dropping the partial bar (the morning scan's approach) ───────────────


def _frame(days: list[date], vols: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="date")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": vols},
        index=idx,
    )


def test_the_partial_bar_is_dropped_intraday() -> None:
    df = _frame([date(2026, 9, 9), TODAY], [100.0, 4.0])
    assert len(drop_partial_bar(df, _at(9, 30))) == 1


def test_a_settled_frame_is_untouched() -> None:
    df = _frame([date(2026, 9, 9), TODAY], [100.0, 99.0])
    assert len(drop_partial_bar(df, _at(16, 0))) == 2


def test_dropping_an_empty_frame_is_safe() -> None:
    empty = _frame([], [])
    assert len(drop_partial_bar(empty, _at(9, 30))) == 0


# ── End to end through the pre-close ratio ───────────────────────────────


def _hist(today_vol: float) -> pd.DataFrame:
    days = [date(2026, 8, 1) + pd.Timedelta(days=i) for i in range(21)]
    days = [d.date() if hasattr(d, "date") else d for d in days[:20]] + [TODAY]
    return _frame(days, [1_000_000.0] * 20 + [today_vol])


def test_preclose_ratio_projects_a_partial_bar() -> None:
    raw = 1.5 * 1_000_000 * 0.851
    assert _volume_ratio(_hist(raw), _at(15, 0)) == pytest.approx(1.5, rel=0.02)


def test_preclose_ratio_leaves_a_settled_bar_alone() -> None:
    assert _volume_ratio(_hist(1_500_000), _at(16, 0)) == pytest.approx(1.5)


def test_preclose_ratio_survives_a_zero_average() -> None:
    df = _frame([date(2026, 8, 1 + i) for i in range(20)] + [TODAY],
                [0.0] * 20 + [500.0])
    assert _volume_ratio(df, _at(15, 0)) == 0.0
