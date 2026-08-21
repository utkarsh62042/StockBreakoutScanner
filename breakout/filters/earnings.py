"""Earnings blackout filter.

Breakouts within a few days of an earnings announcement are really bets on the
result, not on the chart — so the scanner excludes them (a hard pass/fail gate),
UNLESS the breakout *is* the post-earnings reaction (the announcement is already
behind us within the window). The window widens during Indian earnings season
(mid-Jan / mid-Apr / mid-Jul / mid-Oct), when result-day gaps are common.

Earnings dates are best-effort upstream (public sources, occasionally missing);
this module is the pure date logic and treats an empty/None date list as
"no known earnings → not in blackout".
"""

from __future__ import annotations

from datetime import date

import numpy as np


# Months when Indian quarterly results cluster; the blackout widens then.
EARNINGS_SEASON_MONTHS = {1, 4, 7, 10}

_DEFAULT_WINDOW_DAYS = 5        # ± trading days around a normal announcement
_SEASON_WINDOW_DAYS = 7        # wider during earnings season


def trading_days_between(a: date, b: date) -> int:
    """Number of trading (business) days between two dates, order-independent."""
    lo, hi = (a, b) if a <= b else (b, a)
    return int(np.busday_count(lo, hi))


def in_earnings_blackout(
    today: date,
    earnings_dates: list[date] | None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    season_window_days: int = _SEASON_WINDOW_DAYS,
    allow_post_earnings: bool = True,
) -> bool:
    """True if `today` is within the blackout window of any earnings date.

    The window is `season_window_days` during earnings-season months, else
    `window_days`. When `allow_post_earnings` is True, a date that has already
    passed within the window does NOT trigger the blackout — that's the
    post-earnings reaction we explicitly want to trade.
    """
    if not earnings_dates:
        return False
    window = season_window_days if today.month in EARNINGS_SEASON_MONTHS else window_days
    for ed in earnings_dates:
        if ed is None:
            continue
        gap = trading_days_between(today, ed)
        if gap > window:
            continue
        if allow_post_earnings and ed < today:
            continue   # announcement already out — this is the reaction, allow it
        return True
    return False
