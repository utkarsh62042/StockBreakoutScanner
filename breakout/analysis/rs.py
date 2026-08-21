"""Relative strength vs the NIFTY 500 universe.

The scanner wants stocks that are *outperforming* — leaders, not laggards.
We measure each stock's trailing 63-day (≈ one quarter) return, rank the
whole universe cross-sectionally into a 0-100 percentile, and map the top of
that distribution to scoring points.

    RS  = growth_stock / growth_benchmark   (both = 1 + period_return)
    rank = percentile of the stock among all scanned names (0 = worst, 100 = best)

Why rank on returns rather than on the raw RS ratio: every stock is compared
against the *same* benchmark on the same day, so the benchmark is a shared
positive constant and dividing by it does not change the ordering. Ranking the
63-day returns is therefore identical to ranking the RS ratios — and avoids the
divide-by-zero / sign-flip fragility of a literal `stock_ret / bench_ret` when
the benchmark return is near zero or negative. `relative_strength()` is still
provided for anyone who wants the raw RS number.

Scoring (per the spec): rank >= 75 earns the full 15 points; between 50 and 75
it scales linearly; at or below 50 it earns nothing.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import rankdata


_RS_LOOKBACK = 63          # ≈ one quarter of trading days
_RS_FULL_RANK = 75.0       # percentile at/above which full points are earned
_RS_ZERO_RANK = 50.0       # percentile at/below which zero points are earned
_RS_MAX_POINTS = 15.0


def period_return(df: pd.DataFrame, lookback: int = _RS_LOOKBACK) -> float:
    """Trailing `lookback`-bar close-to-close return (e.g. 0.12 = +12%).

    Returns NaN when there isn't enough history.
    """
    if len(df) < lookback + 1:
        return math.nan
    past = float(df["close"].iloc[-lookback - 1])
    now = float(df["close"].iloc[-1])
    if past <= 0:
        return math.nan
    return now / past - 1.0


def relative_strength(stock_return: float, benchmark_return: float) -> float:
    """RS as the ratio of growth factors: (1 + stock) / (1 + benchmark).

    > 1 means the stock outgrew the benchmark over the window. Robust to a
    benchmark return of zero (unlike a literal return-ratio).
    """
    denom = 1.0 + benchmark_return
    if denom <= 0:
        return math.nan
    return (1.0 + stock_return) / denom


def rs_percentile_ranks(returns: dict[str, float]) -> dict[str, float]:
    """Map {symbol: period_return} to {symbol: percentile in [0, 100]}.

    Best return → 100, worst → 0. Ties share the average rank. Symbols whose
    return is NaN are dropped from the ranking (they get no RS percentile).
    A single valid symbol is assigned a neutral 50.
    """
    valid = {s: r for s, r in returns.items() if r is not None and not math.isnan(r)}
    n = len(valid)
    if n == 0:
        return {}
    if n == 1:
        return {next(iter(valid)): 50.0}
    symbols = list(valid.keys())
    values = np.array([valid[s] for s in symbols], dtype=float)
    ranks = rankdata(values, method="average")   # 1 = lowest
    pct = (ranks - 1.0) / (n - 1.0) * 100.0
    return {s: float(p) for s, p in zip(symbols, pct)}


def rs_points(
    percentile: float,
    full_at: float = _RS_FULL_RANK,
    zero_at: float = _RS_ZERO_RANK,
    max_points: float = _RS_MAX_POINTS,
) -> float:
    """Convert an RS percentile to scoring points, clamped to [0, max_points]."""
    if percentile is None or math.isnan(percentile):
        return 0.0
    if percentile >= full_at:
        return max_points
    if percentile <= zero_at:
        return 0.0
    return (percentile - zero_at) / (full_at - zero_at) * max_points
