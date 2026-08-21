"""Fetch the NSE trading-holiday list and write it where the scanner reads it.

Run once at the start of each year (or whenever NSE updates the list):

    .\.venv\Scripts\python.exe scripts\fetch_holidays.py

Writes ISO dates (one per line) to <project root>/holidays.txt, which
breakout.trading_calendar merges with its built-in fixed-date holidays.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from breakout.trading_calendar import DEFAULT_HOLIDAYS_FILE, parse_nse_holidays  # noqa: E402

_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def main() -> int:
    session = requests.Session()
    session.headers.update(_HEADERS)
    # NSE sets cookies on the homepage before the API will answer.
    try:
        session.get("https://www.nseindia.com", timeout=30)
    except requests.RequestException as e:
        print(f"warmup failed (continuing): {e}")

    resp = session.get(_URL, timeout=30)
    resp.raise_for_status()
    dates = parse_nse_holidays(resp.json())
    if not dates:
        print("no holidays parsed — NSE payload shape may have changed", file=sys.stderr)
        return 1

    out = DEFAULT_HOLIDAYS_FILE
    lines = ["# NSE trading holidays (fetched from NSE holiday-master)"]
    lines += [d.isoformat() for d in dates]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(dates)} holidays to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
