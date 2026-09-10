"""Day-by-day progress columns (`days_in_trade` / `daily_moves`).

Entry day is D0; each later day carries that day's own move — D1 off the entry
price, D2 off D1's close, and so on.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from breakout.paper.tracker import compute_daily_progress


def _prices(start: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D", name="date")
    return pd.DataFrame({"close": closes}, index=idx)


def test_entry_day_is_d0_and_carries_no_move() -> None:
    prices = _prices("2026-08-03", [101.0])
    assert compute_daily_progress("2026-08-03", 100.0, prices) == ("D0", None)


def test_d1_off_entry_price_then_day_over_day() -> None:
    # D0 close 101 is only the entry-day bar; D1 measures 100 -> 104.
    prices = _prices("2026-08-03", [101.0, 104.0, 101.0])
    label, moves = compute_daily_progress("2026-08-03", 100.0, prices)
    assert label == "D2"
    assert moves == "D1:4.0%,D2:-2.9%"


def test_bars_before_entry_are_ignored() -> None:
    prices = _prices("2026-08-01", [90.0, 95.0, 101.0, 102.0, 100.98])
    label, moves = compute_daily_progress("2026-08-03", 100.0, prices)
    assert label == "D2"
    assert moves == "D1:2.0%,D2:-1.0%"


def test_bars_after_today_are_ignored() -> None:
    prices = _prices("2026-08-03", [101.0, 104.0, 110.0])
    label, moves = compute_daily_progress(
        "2026-08-03", 100.0, prices, date(2026, 8, 4)
    )
    assert label == "D1"
    assert moves == "D1:4.0%"


def test_blank_when_not_entered_or_no_bars() -> None:
    prices = _prices("2026-08-03", [101.0])
    assert compute_daily_progress(None, None, prices) == (None, None)
    assert compute_daily_progress("2026-08-03", None, prices) == (None, None)
    # Entry is in the future relative to every cached bar.
    assert compute_daily_progress("2026-09-01", 100.0, prices) == (None, None)
    empty = pd.DataFrame(columns=["close"])
    assert compute_daily_progress("2026-08-03", 100.0, empty) == (None, None)
