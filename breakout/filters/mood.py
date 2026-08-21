"""Market-mood context: sector-index trend and India VIX.

Two independent signals feed the scanner:

  * **Sector trend** — a stock breaking out while its sector index is rising has
    the wind at its back. `index_trend` classifies a sector index (or any index)
    as 'up' / 'flat' / 'down' from its close vs a rising/falling SMA. The label
    strings match what `scoring.composite_score` expects.

  * **India VIX** — a broad risk gauge. Elevated VIX = risk-off; breakouts fail
    more often. `vix_mood` labels the regime and `market_tradeable` is a soft
    gate the jobs can consult.

Both are pure functions over data passed in; fetching the index / VIX series is
the caller's job (wired in a later chunk).
"""

from __future__ import annotations

import math

import pandas as pd

from breakout.analysis.indicators import sma


# Canonical sectors we track, keyed by substrings that appear in NSE's
# "Industry" field. Best-effort: unmatched industries return None.
SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BANK": ("bank",),
    "IT": ("information technology", "software", " it "),
    "AUTO": ("auto", "automobile"),
    "PHARMA": ("pharma", "healthcare", "drug"),
    "FMCG": ("fmcg", "consumer goods", "food", "personal"),
    "METAL": ("metal", "steel", "mining", "iron"),
    "REALTY": ("realty", "real estate", "construction"),
    "ENERGY": ("energy", "power", "oil", "gas", "petroleum"),
    "FINANCE": ("finance", "financial", "nbfc", "insurance"),
}


# Canonical sector -> yfinance index symbol. Best-effort; a symbol that fails
# to fetch just falls back to the caller's proxy / 'flat'.
SECTOR_INDEX_SYMBOLS: dict[str, str] = {
    "BANK": "^NSEBANK",
    "IT": "^CNXIT",
    "AUTO": "^CNXAUTO",
    "PHARMA": "^CNXPHARMA",
    "FMCG": "^CNXFMCG",
    "METAL": "^CNXMETAL",
    "REALTY": "^CNXREALTY",
    "ENERGY": "^CNXENERGY",
    "FINANCE": "^CNXFIN",   # NOTE: does not resolve on yfinance — this sector
                            # falls back to the member-return proxy until a
                            # working NIFTY Financial Services symbol is found.
}


def classify_sector(industry: str | None) -> str | None:
    """Map an NSE industry string to one of our canonical sectors, or None."""
    if not industry:
        return None
    text = f" {industry.lower()} "
    for sector, needles in SECTOR_KEYWORDS.items():
        if any(nd in text for nd in needles):
            return sector
    return None


def index_trend(
    df: pd.DataFrame,
    sma_period: int = 50,
    slope_lookback: int = 20,
    flat_band: float = 0.005,
) -> str:
    """Classify an index as 'up', 'flat', or 'down'.

    'up'   : last close above a rising SMA (SMA up > flat_band over the lookback)
    'down' : last close below a falling SMA
    'flat' : anything else, or insufficient history.
    """
    if len(df) < sma_period + slope_lookback:
        return "flat"
    close = df["close"]
    s = sma(close, sma_period)
    sma_now = s.iloc[-1]
    sma_past = s.iloc[-1 - slope_lookback]
    if pd.isna(sma_now) or pd.isna(sma_past) or sma_past <= 0:
        return "flat"
    slope = (sma_now - sma_past) / sma_past
    above = float(close.iloc[-1]) > float(sma_now)
    if above and slope > flat_band:
        return "up"
    if not above and slope < -flat_band:
        return "down"
    return "flat"


def vix_mood(
    vix_now: float,
    vix_avg: float,
    high_mult: float = 1.25,
    low_mult: float = 0.85,
) -> str:
    """Regime label from India VIX vs its own average: 'risk_on' / 'neutral' /
    'risk_off'."""
    if vix_now is None or math.isnan(vix_now) or vix_avg is None or math.isnan(vix_avg) or vix_avg <= 0:
        return "neutral"
    if vix_now >= vix_avg * high_mult:
        return "risk_off"
    if vix_now <= vix_avg * low_mult:
        return "risk_on"
    return "neutral"


def fetch_sector_trends(sectors, fetch=None, min_bars: int = 70) -> dict[str, str]:
    """Fetch each canonical sector's index and classify its trend.

    Returns {sector: 'up'|'flat'|'down'} only for sectors whose index fetched
    with enough history. `fetch` is injectable (defaults to the yfinance index
    fetch) so this is testable without network.
    """
    if fetch is None:
        from breakout.data.fetcher import fetch_yf_index as fetch
    out: dict[str, str] = {}
    for sector in set(sectors):
        symbol = SECTOR_INDEX_SYMBOLS.get(sector)
        if not symbol:
            continue
        series = fetch(symbol, days=150)
        if series is None or len(series) < min_bars:
            continue
        out[sector] = index_trend(pd.DataFrame({"close": list(series)}))
    return out


def assess_market(vix_close, ceiling: float = 25.0, avg_period: int = 20) -> dict:
    """Summarise market mood from an India-VIX close Series.

    Returns {'vix': float|None, 'mood': str, 'tradeable': bool}. A None/empty
    series yields a neutral, tradeable default (we never block on missing data).
    """
    if vix_close is None or len(vix_close) == 0:
        return {"vix": None, "mood": "neutral", "tradeable": True}
    now = float(vix_close.iloc[-1])
    avg = float(vix_close.tail(avg_period).mean())
    return {"vix": now, "mood": vix_mood(now, avg), "tradeable": market_tradeable(now, ceiling)}


def market_tradeable(vix_now: float | None, ceiling: float = 25.0) -> bool:
    """Soft gate: False when India VIX is elevated above `ceiling` (fear high).

    Unknown VIX (None/NaN) defaults to tradeable — we don't block on missing
    data.
    """
    if vix_now is None or (isinstance(vix_now, float) and math.isnan(vix_now)):
        return True
    return vix_now <= ceiling
