"""Market-mood filter tests."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import pandas as pd

from breakout.filters.mood import (
    assess_market,
    classify_sector,
    fetch_sector_trends,
    index_trend,
    market_tradeable,
    vix_mood,
)


def _idx(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1.0] * n}
    )


def test_index_trend_up() -> None:
    assert index_trend(_idx(list(np.linspace(80, 120, 100)))) == "up"


def test_index_trend_down() -> None:
    assert index_trend(_idx(list(np.linspace(120, 80, 100)))) == "down"


def test_index_trend_flat() -> None:
    assert index_trend(_idx([100.0] * 100)) == "flat"


def test_index_trend_insufficient_history() -> None:
    assert index_trend(_idx([100.0] * 10)) == "flat"


def test_classify_sector_matches_keywords() -> None:
    assert classify_sector("Private Sector Bank") == "BANK"
    assert classify_sector("Pharmaceuticals") == "PHARMA"
    assert classify_sector("Refineries / Petroleum") == "ENERGY"


def test_classify_sector_unknown_is_none() -> None:
    assert classify_sector("Diversified Widgets") is None
    assert classify_sector(None) is None


def test_vix_mood_labels() -> None:
    assert vix_mood(30.0, 15.0) == "risk_off"     # 2x average
    assert vix_mood(12.0, 15.0) == "risk_on"      # well below
    assert vix_mood(15.5, 15.0) == "neutral"


def test_vix_mood_missing_is_neutral() -> None:
    assert vix_mood(math.nan, 15.0) == "neutral"


def test_market_tradeable_gate() -> None:
    assert market_tradeable(18.0) is True
    assert market_tradeable(30.0) is False
    assert market_tradeable(None) is True         # missing data → don't block


def test_assess_market_none_is_neutral_tradeable() -> None:
    a = assess_market(None)
    assert a["vix"] is None and a["mood"] == "neutral" and a["tradeable"] is True


def test_assess_market_calm() -> None:
    a = assess_market(pd.Series([15.0] * 20))
    assert a["tradeable"] is True and a["mood"] == "neutral"


def test_assess_market_risk_off_and_untradeable() -> None:
    a = assess_market(pd.Series([15.0] * 19 + [30.0]))   # spike well above avg and ceiling
    assert a["mood"] == "risk_off" and a["tradeable"] is False and a["vix"] == 30.0


def test_fetch_sector_trends_with_injected_fetch() -> None:
    def fake(symbol, days=150):
        if symbol == "^NSEBANK":
            return pd.Series(list(np.linspace(80, 120, 120)))   # rising → up
        if symbol == "^CNXIT":
            return pd.Series(list(np.linspace(120, 80, 120)))   # falling → down
        return None                                             # PHARMA fails

    out = fetch_sector_trends(["BANK", "IT", "PHARMA", "NOT_A_SECTOR"], fetch=fake)
    assert out["BANK"] == "up"
    assert out["IT"] == "down"
    assert "PHARMA" not in out            # index returned None
    assert "NOT_A_SECTOR" not in out      # no symbol mapping


def test_fetch_sector_trends_skips_short_series() -> None:
    out = fetch_sector_trends(["BANK"], fetch=lambda s, days=150: pd.Series([100.0] * 10))
    assert out == {}                      # too few bars
