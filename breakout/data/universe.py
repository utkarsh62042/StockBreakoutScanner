"""NIFTY 500 universe loader.

Fetches the constituent list from NSE's official CSV archive and caches it
in SQLite. Refreshes weekly (configurable via `cfg.thresholds`-style knobs);
the universe rarely changes day-to-day so we don't refetch on every run.

NSE blocks anonymous downloads without a browser-like User-Agent — we set
one explicitly. The CSV columns we care about are `Symbol` (NSE ticker
without suffix), `Company Name`, and `Industry` (NSE's sector tag).
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

import requests

from breakout.data.store import Store


logger = logging.getLogger(__name__)


NIFTY_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Universe is refreshed if cached version is older than this many days.
DEFAULT_REFRESH_DAYS = 7


def refresh_universe_if_stale(
    store: Store,
    refresh_days: int = DEFAULT_REFRESH_DAYS,
    timeout: int = 30,
) -> tuple[int, bool]:
    """Refresh the NIFTY 500 universe in `store` if older than `refresh_days`.

    Returns:
        (count, refreshed). `count` is the number of symbols in the universe
        after the call. `refreshed` is True iff we actually downloaded
        a fresh copy this call.
    """
    age = store.universe_age_days()
    if age is not None and age < refresh_days:
        logger.info(f"universe cache is {age} days old, skipping refresh")
        return len(store.read_universe()), False

    logger.info(f"fetching NIFTY 500 universe from {NIFTY_500_URL}")
    rows = _download_nifty500(timeout=timeout)
    written = store.upsert_universe(rows)
    logger.info(f"universe refreshed: {written} symbols written")
    return written, True


def _download_nifty500(timeout: int = 30) -> list[dict]:
    """Download and parse the NIFTY 500 CSV from NSE archives."""
    # NSE sometimes redirects to a login or cookie-set page on the very
    # first request — establishing a session and hitting the homepage
    # first lets the cookies settle. This is a documented quirk of NSE's
    # archive endpoints.
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=timeout)
    except requests.RequestException as e:
        logger.warning(f"NSE homepage warmup failed (continuing): {e}")

    resp = session.get(NIFTY_500_URL, timeout=timeout)
    resp.raise_for_status()

    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "company_name": (row.get("Company Name") or "").strip(),
                "sector": (row.get("Industry") or "").strip(),
                "industry": (row.get("Industry") or "").strip(),
                "market_cap_cr": None,  # NSE CSV doesn't include this field
            }
        )

    if not rows:
        raise RuntimeError("NIFTY 500 CSV returned 0 rows — schema may have changed")
    return rows


def load_fallback_universe(path: Path) -> list[dict]:
    """Read a manually-curated CSV when NSE is unreachable.

    The file should have at least a Symbol column (and optionally Company
    Name, Industry). Useful for development behind firewalls or when NSE
    rate-limits anonymous downloads.
    """
    if not path.exists():
        raise FileNotFoundError(f"fallback universe file not found at {path}")
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = (row.get("Symbol") or row.get("symbol") or "").strip()
            if not sym:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "company_name": (row.get("Company Name") or row.get("company_name") or "").strip(),
                    "sector": (row.get("Industry") or row.get("sector") or "").strip(),
                    "industry": (row.get("Industry") or row.get("industry") or "").strip(),
                    "market_cap_cr": None,
                }
            )
    return rows
