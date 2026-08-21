"""Stan Weinstein stage classification.

Weinstein's framework divides a stock's life into four stages:
    Stage 1 — Accumulation: price oscillates around a flat 30-week SMA.
    Stage 2 — Advance:     price > rising 30-week SMA, with higher highs.
    Stage 3 — Distribution: price oscillates around a flattening SMA.
    Stage 4 — Decline:     price < falling 30-week SMA.

The scanner only emits alerts for Stage 2 stocks — historically the most
reliable filter for swing breakouts, eliminating ~70% of false signals
(stocks breaking out into resistance from extended bases or downtrends).
"""

from __future__ import annotations

from enum import Enum

import pandas as pd


class Stage(str, Enum):
    STAGE_1 = "STAGE_1"
    STAGE_2 = "STAGE_2"
    STAGE_3 = "STAGE_3"
    STAGE_4 = "STAGE_4"
    UNKNOWN = "UNKNOWN"


# 30 weeks * 5 trading days = 150 trading days.
_DEFAULT_SMA_BARS = 150
# Slope is measured over the last 10 weeks (~50 trading days).
_DEFAULT_SLOPE_LOOKBACK_BARS = 50
# Higher-high check window — must beat the prior 10-week peak.
_DEFAULT_HIGH_LOOKBACK_BARS = 50

# Slope thresholds (as a fraction of the SMA's current value, per bar).
# 0.0005/bar ≈ 12.5% per year — the minimum SMA rise rate we'd consider
# meaningful for a Stage 2 trend. Tighter values mis-classify slow advances
# as Stage 1; looser values let weak drifts qualify.
_FLAT_SLOPE_BAND = 0.0005


def classify_stage(
    df: pd.DataFrame,
    sma_bars: int = _DEFAULT_SMA_BARS,
    slope_lookback_bars: int = _DEFAULT_SLOPE_LOOKBACK_BARS,
    high_lookback_bars: int = _DEFAULT_HIGH_LOOKBACK_BARS,
) -> Stage:
    """Returns the Weinstein stage of the most recent bar.

    Rules:
        - Stage 2 requires: close > SMA, SMA rising over the slope window,
          and the recent 10-week high > the prior 10-week high.
        - Stage 4 requires: close < SMA AND SMA falling.
        - Stage 1: close near a flat SMA after a prior decline.
        - Stage 3: close near a flat SMA after a prior advance.
        - UNKNOWN if the data window is too short to classify.

    Returns:
        Stage enum value. Calling code typically only acts on STAGE_2.
    """
    if len(df) < sma_bars + slope_lookback_bars:
        return Stage.UNKNOWN

    close = df["close"]
    sma_150 = close.rolling(window=sma_bars, min_periods=sma_bars).mean()
    if pd.isna(sma_150.iloc[-1]):
        return Stage.UNKNOWN

    today_close = float(close.iloc[-1])
    today_sma = float(sma_150.iloc[-1])
    prior_sma = float(sma_150.iloc[-slope_lookback_bars])
    # Normalize the slope so the threshold is regime-independent.
    slope_per_bar = (today_sma - prior_sma) / prior_sma / slope_lookback_bars

    above_sma = today_close > today_sma
    rising_sma = slope_per_bar > _FLAT_SLOPE_BAND
    falling_sma = slope_per_bar < -_FLAT_SLOPE_BAND
    flat_sma = not rising_sma and not falling_sma

    # Stage 2: price above a rising SMA, AND recent peak > prior peak
    if above_sma and rising_sma:
        if len(close) >= 2 * high_lookback_bars:
            recent_peak = float(close.iloc[-high_lookback_bars:].max())
            prior_peak = float(close.iloc[-2 * high_lookback_bars : -high_lookback_bars].max())
            higher_high = recent_peak > prior_peak
        else:
            higher_high = True  # not enough history; permissive on this check
        if higher_high:
            return Stage.STAGE_2

    # Stage 4: price below a falling SMA
    if not above_sma and falling_sma:
        return Stage.STAGE_4

    # Stage 1 vs Stage 3: SMA is flat. Decide by looking at the prior trend.
    if flat_sma:
        # Compare the SMA from ~3 months ago to now: was it falling (=Stage 1)
        # or rising (=Stage 3) before flattening?
        prior_window = 3 * slope_lookback_bars
        if len(close) >= prior_window:
            very_prior_sma = float(sma_150.iloc[-prior_window])
            if not pd.isna(very_prior_sma):
                if very_prior_sma > prior_sma:
                    # SMA was higher 3 months ago — flattening after a decline
                    return Stage.STAGE_1
                else:
                    return Stage.STAGE_3
        return Stage.STAGE_1  # fall-through default — conservative

    # If we reach here, conditions don't cleanly map: pick the conservative
    # non-Stage-2 bucket so the alert path is closed.
    return Stage.STAGE_3 if above_sma else Stage.STAGE_4
