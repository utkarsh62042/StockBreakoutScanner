"""Volatility-contraction ("tightness") measurement.

A base that tightens before a breakout — shrinking daily ranges — tends to
break out more cleanly, because supply has been absorbed and there's little
overhead resistance left to chew through. We quantify that as the ratio of
recent to longer-run ATR.

    tightness_score = 1 - ATR(short) / ATR(long)          (default 10 / 50)

Interpretation:
    > 0.3   strong contraction (recent range is <70% of the longer-run range)
    ~ 0     normal
    < 0     expansion (recent range wider than the longer-run range)

The scoring engine converts this to at most 10 points (`tightness_points`).
"""

from __future__ import annotations

import math

import pandas as pd

from breakout.analysis.indicators import atr


_TIGHTNESS_SHORT = 10
_TIGHTNESS_LONG = 50
_TIGHTNESS_MAX_POINTS = 10.0


def tightness_score(
    df: pd.DataFrame,
    short: int = _TIGHTNESS_SHORT,
    long: int = _TIGHTNESS_LONG,
) -> float:
    """Return `1 - ATR(short)/ATR(long)` on the last bar.

    Positive = contracting (tight), negative = expanding. Returns 0.0 when
    there isn't enough history to compute the longer ATR, or when the long
    ATR is non-positive (degenerate flat series).
    """
    if len(df) < long + 1:
        return 0.0
    atr_short = atr(df, short).iloc[-1]
    atr_long = atr(df, long).iloc[-1]
    if pd.isna(atr_short) or pd.isna(atr_long) or atr_long <= 0:
        return 0.0
    return float(1.0 - atr_short / atr_long)


def tightness_points(score: float, max_points: float = _TIGHTNESS_MAX_POINTS) -> float:
    """Map a tightness score to scoring points, clamped to [0, max_points].

    Expansion (negative score) contributes 0 — it's the opposite of the
    setup we want, not a penalty to be carried as negative points.
    """
    if score is None or math.isnan(score):
        return 0.0
    return max(0.0, min(max_points, score * max_points))
