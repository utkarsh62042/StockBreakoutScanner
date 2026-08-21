"""NSE trading-calendar helpers.

Jobs must not run on weekends or exchange holidays. `is_trading_day` combines a
weekday check with a holiday set. The built-in set covers the fixed-date NSE
holidays; the lunar-calendar ones (Holi, Diwali, Id, etc.) shift each year and
MUST be topped up from the NSE annual circular — either edit `NSE_HOLIDAYS` or
point `load_holidays` at a text file of ISO dates (one per line).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
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
