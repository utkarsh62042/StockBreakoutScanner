"""Stage classifier tests.

These test the four canonical regimes: linear uptrend, downtrend, flat
post-decline (Stage 1) and flat post-advance (Stage 3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from breakout.analysis.stage import Stage, classify_stage


def _ohlcv_from_close(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100000] * len(closes),
        }
    )


def test_clear_uptrend_classified_stage_2() -> None:
    closes = list(np.linspace(100, 200, 250))
    df = _ohlcv_from_close(closes)
    assert classify_stage(df) == Stage.STAGE_2


def test_clear_downtrend_classified_stage_4() -> None:
    closes = list(np.linspace(200, 100, 250))
    df = _ohlcv_from_close(closes)
    assert classify_stage(df) == Stage.STAGE_4


def test_insufficient_data_returns_unknown() -> None:
    closes = list(np.linspace(100, 110, 50))
    df = _ohlcv_from_close(closes)
    assert classify_stage(df) == Stage.UNKNOWN


def test_flat_after_decline_classified_stage_1() -> None:
    """Big decline, then flat — accumulation."""
    decline = list(np.linspace(200, 100, 150))
    flat = list(np.linspace(100, 102, 150)) + [101.5] * 50
    df = _ohlcv_from_close(decline + flat)
    assert classify_stage(df) == Stage.STAGE_1


def test_flat_after_advance_classified_stage_3() -> None:
    """Big advance, then flat — distribution."""
    advance = list(np.linspace(100, 200, 150))
    flat = list(np.linspace(200, 198, 150)) + [199] * 50
    df = _ohlcv_from_close(advance + flat)
    assert classify_stage(df) == Stage.STAGE_3


def test_recent_breakout_from_long_base_classified_stage_2() -> None:
    """The classic Weinstein Stage 1 -> Stage 2 transition."""
    # Long flat base around 100, then breakout to 120
    base = [100 + np.sin(i / 5) * 1.5 for i in range(200)]
    breakout = list(np.linspace(101, 125, 80))
    df = _ohlcv_from_close(base + breakout)
    assert classify_stage(df) == Stage.STAGE_2


def test_stage_2_requires_close_above_sma() -> None:
    """A pullback through the SMA, even in an uptrend, shouldn't be Stage 2."""
    # Trend up 200 bars, then a sharp drop below the SMA
    trend = list(np.linspace(100, 180, 200))
    drop = list(np.linspace(180, 135, 50))  # close ends well below SMA
    df = _ohlcv_from_close(trend + drop)
    result = classify_stage(df)
    assert result != Stage.STAGE_2
