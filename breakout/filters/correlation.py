"""Sector and correlation-based position sizing adjustments.

When multiple open positions are in the same sector or have high correlation,
the portfolio's actual exposure is not 8 independent 1% risks, but rather
8 leveraged bets on the same asset class. This module detects correlation
and scales position sizes down accordingly.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd


def get_sector_exposure(
    open_trades: list[dict],
    universe: dict[str, dict],
) -> dict[str, int]:
    """Count open positions by sector.

    Args:
        open_trades: List of open paper trades
        universe: Dict mapping symbol -> universe metadata

    Returns:
        Dict of sector -> count
    """
    sector_counts: dict[str, int] = defaultdict(int)
    for trade in open_trades:
        symbol = trade.get("symbol")
        if symbol:
            meta = universe.get(symbol)
            if meta:
                sector = meta.get("sector") or "unknown"
                sector_counts[sector] += 1
    return dict(sector_counts)


def correlation_scaling_factor(
    sector: str,
    sector_exposure: dict[str, int],
    max_per_sector: int = 3,
    base_multiplier: float = 1.0,
) -> float:
    """Calculate position size multiplier based on sector concentration.

    If a sector already has N positions and you're about to open another,
    scale down the new position to reduce correlation risk.

    Args:
        sector: The sector of the new position
        sector_exposure: Current sector exposure counts
        max_per_sector: Max positions per sector (from config)
        base_multiplier: Starting multiplier (usually 1.0, may be 0.5+ from stress)

    Returns:
        Multiplier in [0.0, 1.0] to apply to position size
    """
    if sector not in sector_exposure:
        # New sector: no concentration yet
        return base_multiplier

    count = sector_exposure[sector]

    if count >= max_per_sector - 1:
        # About to hit limit: scale to 25% (very cautious)
        return base_multiplier * 0.25
    elif count >= max_per_sector - 2:
        # Approaching limit: scale to 50% (cautious)
        return base_multiplier * 0.50
    elif count >= 1:
        # Already have 1-2 in this sector: scale to 75% (mild caution)
        return base_multiplier * 0.75

    return base_multiplier


def estimate_portfolio_correlation(
    returns_series: dict[str, pd.Series],
    window: int = 63,
) -> float:
    """Estimate average correlation between open positions.

    A rough heuristic: average pairwise correlation across all open positions.
    High correlation (>0.7) suggests they're moving together and the portfolio
    is effectively a concentrated bet, not a diversified portfolio.

    Args:
        returns_series: Dict of symbol -> return series (daily % returns)
        window: Look-back period for correlation (default 63 days)

    Returns:
        Average correlation in [0.0, 1.0]. 0 = no correlation, 1 = perfect
    """
    if len(returns_series) < 2:
        return 0.0

    symbols = list(returns_series.keys())
    correlations: list[float] = []

    for i, sym1 in enumerate(symbols[:-1]):
        for sym2 in symbols[i + 1 :]:
            r1 = returns_series[sym1].tail(window)
            r2 = returns_series[sym2].tail(window)

            # Ensure we have overlapping data
            common_idx = r1.index.intersection(r2.index)
            if len(common_idx) < 10:
                continue

            r1_aligned = r1[common_idx]
            r2_aligned = r2[common_idx]

            # Correlation between return series
            corr = r1_aligned.corr(r2_aligned)
            if pd.notna(corr):
                correlations.append(abs(corr))  # Use absolute correlation

    if not correlations:
        return 0.0

    return sum(correlations) / len(correlations)


def position_size_scaled_for_correlation(
    base_position_size: int,
    avg_correlation: float,
    sector_multiplier: float = 1.0,
    max_correlation_discount: float = 0.5,
) -> int:
    """Scale position size down based on portfolio correlation.

    If positions are highly correlated, reduce the new position to avoid
    compounding risk.

    Args:
        base_position_size: Position size before correlation adjustment
        avg_correlation: Average correlation (0.0 to 1.0)
        sector_multiplier: Already-applied sector concentration scaling
        max_correlation_discount: Maximum discount to apply (default 50%)

    Returns:
        Adjusted position size
    """
    if avg_correlation < 0.3:
        # Low correlation: no additional discount
        correlation_multiplier = 1.0
    elif avg_correlation < 0.5:
        # Moderate correlation: slight discount (80%)
        correlation_multiplier = 0.80
    elif avg_correlation < 0.7:
        # High correlation: significant discount (60%)
        correlation_multiplier = 0.60
    else:
        # Very high correlation: heavy discount (50%)
        correlation_multiplier = max(1.0 - max_correlation_discount, 0.5)

    final_multiplier = sector_multiplier * correlation_multiplier
    adjusted_size = int(base_position_size * final_multiplier)

    return max(0, adjusted_size)
