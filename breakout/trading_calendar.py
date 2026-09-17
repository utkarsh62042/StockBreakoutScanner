"""NSE trading-calendar helpers.

Jobs must not run on weekends or exchange holidays. `is_trading_day` combines a
weekday check with a holiday set. The built-in set covers the fixed-date NSE
holidays; the lunar-calendar ones (Holi, Diwali, Id, etc.) shift each year and
MUST be topped up from the NSE annual circular — either edit `NSE_HOLIDAYS` or
point `load_holidays` at a text file of ISO dates (one per line).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Holidays fetched by scripts/fetch_holidays.py land here and are merged with the
# built-in fixed-date set automatically. Run that script at the start of each year.
DEFAULT_HOLIDAYS_FILE = Path(__file__).resolve().parent.parent / "holidays.txt"


# Fixed-date NSE holidays. VERIFY / TOP UP each year from the NSE circular —
# this omits the lunar-calendar festivals, which move annually.
NSE_HOLIDAYS: set[date] = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 12, 25),  # Christmas
}


def active_holidays(path: Path | str | None = None) -> set[date]:
    """Built-in fixed-date holidays merged with the fetched holidays file."""
    return NSE_HOLIDAYS | load_holidays(path or DEFAULT_HOLIDAYS_FILE)


def is_trading_day(d: date, holidays: set[date] | None = None) -> bool:
    """True if `d` is a weekday and not an exchange holiday.

    With `holidays=None` the built-in set plus the fetched holidays file are
    used; pass an explicit set to override entirely."""
    holidays = active_holidays() if holidays is None else holidays
    return d.weekday() < 5 and d not in holidays


def require_trading_day(d: date, holidays: set[date] | None = None) -> bool:
    """Convenience gate for job entry points: logs and returns False when `d`
    isn't a trading day."""
    if is_trading_day(d, holidays):
        return True
    logger.info(f"{d} is not an NSE trading day (weekend/holiday) — skipping")
    return False


def previous_trading_day(d: date, holidays: set[date] | None = None) -> date:
    """The most recent trading day strictly before `d`.

    Walks backwards a day at a time; the bound of 10 is well clear of the
    longest run of NSE closures (a holiday adjoining a weekend) and stops a bad
    holidays file from looping forever.
    """
    holidays = active_holidays() if holidays is None else holidays
    cur = d
    for _ in range(10):
        cur = cur - timedelta(days=1)
        if is_trading_day(cur, holidays):
            return cur
    return d - timedelta(days=1)


def last_completed_session(
    now=None, holidays: set[date] | None = None
) -> date:
    """The latest trading day whose bar is finished.

    Today once the close has passed, otherwise the previous trading day. This is
    the date a *settled* bar should carry, and so the yardstick for whether a
    symbol's cached data is fresh enough to analyse — "is the latest bar today?"
    is the wrong question before 3:30 PM, when today's bar is still being
    written and is deliberately excluded from the morning scan.
    """
    from breakout.analysis.session import SESSION_CLOSE, now_ist

    now = now or now_ist()
    holidays = active_holidays() if holidays is None else holidays
    today = now.date()
    if is_trading_day(today, holidays) and now.time() >= SESSION_CLOSE:
        return today
    return previous_trading_day(today, holidays)


def parse_nse_holidays(payload: dict) -> list[date]:
    """Parse NSE's holiday-master JSON into a sorted list of dates.

    The API returns per-segment lists (e.g. {"CM": [{"tradingDate": "26-Jan-2026",
    ...}]}). We read the cash-market ("CM") segment, falling back to the first
    list found. Unparseable rows are skipped."""
    rows = payload.get("CM") if isinstance(payload, dict) else None
    if not rows:
        rows = next((v for v in (payload or {}).values() if isinstance(v, list)), [])
    out: set[date] = set()
    for row in rows or []:
        td = (row or {}).get("tradingDate")
        if not td:
            continue
        try:
            out.add(datetime.strptime(td, "%d-%b-%Y").date())
        except (TypeError, ValueError):
            continue
    return sorted(out)


def load_holidays(path: Path | str) -> set[date]:
    """Read a holiday file (one ISO date per line; blanks/`#` comments ignored)."""
    out: set[date] = set()
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.add(date.fromisoformat(line[:10]))
        except ValueError:
            logger.warning(f"ignoring bad holiday line: {line!r}")
    return out
