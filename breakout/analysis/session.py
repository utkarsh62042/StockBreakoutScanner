"""NSE session clock and the intraday volume profile.

Both scans compare *today's* volume against a 20-day average of *full* days.
When today's bar is still partial that comparison is apples-to-oranges, and it
is biased in one direction only: the partial bar is always too small, so the
1.5x confirmation gate is systematically harder to clear than intended and the
volume score reads low. Since the volume component now actually discriminates
(it used to saturate at the gate and contribute nothing), this bias matters more
than it did.

The obvious correction — scale by elapsed clock time — is wrong, because NSE
volume is not uniform across the session. It is U-shaped: heavy at the open,
heavy into the close, thin through the middle of the day.

Measured over 276 symbol-days of 5-minute bars across 12 large NSE names
(2026-09-12), cumulative share of the session's volume completed by each mark,
against what elapsed clock time alone would predict:

        time    measured   clock-time
        09:30     0.039       0.040
        10:30     0.181       0.200
        11:30     0.316       0.360
        12:30     0.471       0.520
        13:30     0.609       0.680
        14:30     0.747       0.840
        15:00     0.851       0.920
        15:30     1.000       1.000

The closing half hour carries ~15% of the day's volume in 8% of its minutes.
Clock time overstates progress all the way through the session — at 3 PM it says
92% when the truth is 85%, so a genuine 1.5x day reads as 1.27x and fails the
gate.

These are medians across liquid large caps. A thin name's profile will differ,
and the figures will drift as market structure changes; they are a much better
estimate than the implicit 1.00 the code used before, not a precise constant.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


#: NSE equities trade 09:15-15:30 IST.
IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SESSION_MINUTES = 375

#: Measured cumulative volume profile: (minutes since open, share of day's
#: volume completed). Interpolated linearly between points.
VOLUME_PROFILE: list[tuple[int, float]] = [
    (0, 0.000),
    (15, 0.039),    # 09:30
    (45, 0.117),    # 10:00
    (75, 0.181),    # 10:30
    (105, 0.243),   # 11:00
    (135, 0.316),   # 11:30
    (165, 0.395),   # 12:00
    (195, 0.471),   # 12:30
    (225, 0.543),   # 13:00
    (255, 0.609),   # 13:30
    (285, 0.671),   # 14:00
    (315, 0.747),   # 14:30
    (345, 0.851),   # 15:00
    (375, 1.000),   # 15:30
]

#: Below this much of the session, do not project — divide a tiny partial by a
#: tiny fraction and you amplify noise rather than remove bias. At the 9:30
#: morning scan only ~4% of the day has traded; projecting from that multiplies
#: whatever the first fifteen minutes happened to do by ~26. Such bars should be
#: excluded from the comparison instead (see `drop_partial_bar`).
MIN_PROJECTABLE_FRACTION = 0.5


def now_ist() -> datetime:
    """Current time in Asia/Kolkata, regardless of the machine's timezone."""
    return datetime.now(IST)


def today_ist() -> date:
    """Today's date in Asia/Kolkata, regardless of the machine's timezone."""
    return now_ist().date()


def minutes_into_session(at: datetime) -> int:
    """Minutes elapsed since the open, clamped to [0, SESSION_MINUTES]."""
    local = at.astimezone(IST) if at.tzinfo else at.replace(tzinfo=IST)
    elapsed = (local.hour * 60 + local.minute) - (
        SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
    )
    return max(0, min(SESSION_MINUTES, elapsed))


def session_volume_fraction(at: datetime) -> float:
    """Share of a typical session's volume completed by `at`.

    1.0 once the session has closed, so post-close callers need no special case.
    """
    m = minutes_into_session(at)
    prev_m, prev_f = VOLUME_PROFILE[0]
    for cur_m, cur_f in VOLUME_PROFILE[1:]:
        if m <= cur_m:
            span = cur_m - prev_m
            if span <= 0:
                return cur_f
            return prev_f + (m - prev_m) / span * (cur_f - prev_f)
        prev_m, prev_f = cur_m, cur_f
    return 1.0


def is_partial_bar(bar_date: date | None, at: datetime | None = None) -> bool:
    """True if a bar dated `bar_date` is still being written.

    Only today's bar, and only before the close. Anything else is settled.
    """
    if bar_date is None:
        return False
    at = at or now_ist()
    local = at.astimezone(IST) if at.tzinfo else at.replace(tzinfo=IST)
    return bar_date == local.date() and local.time() < SESSION_CLOSE


def project_full_day_volume(
    partial_volume: float, at: datetime | None = None
) -> tuple[float, float]:
    """Scale a partial day's volume to a full-day estimate.

    Returns `(projected_volume, fraction_used)`. Below
    `MIN_PROJECTABLE_FRACTION` the volume is returned unchanged with the real
    fraction, so callers can see that no projection was applied.

    Equivalent to scaling the 20-day average *down* by the same fraction; the
    ratio is identical either way. Projecting up is the more legible of the two
    because the number it produces ("this is tracking toward 2.1x average") is
    the one a trader would actually reason about.
    """
    at = at or now_ist()
    frac = session_volume_fraction(at)
    if frac < MIN_PROJECTABLE_FRACTION or frac <= 0:
        return partial_volume, frac
    return partial_volume / frac, frac


def drop_partial_bar(df, at: datetime | None = None):
    """Return `df` without a final bar that is still being written.

    For the 9:30 morning scan, where projection is not viable: a 15-minute bar
    compared against 20 full days would read as a ~0.04x volume ratio and drag
    the candidate's score down for no reason other than the clock. Excluding it
    scores the setup on completed history, which is what the morning scan is
    for — the pre-close scan does the real volume check.
    """
    if df is None or len(df) == 0:
        return df
    last = df.index[-1]
    last_date = last.date() if hasattr(last, "date") else None
    if is_partial_bar(last_date, at):
        return df.iloc[:-1]
    return df
