"""Quality floor filter.

A hard pass/fail gate run before scoring. Filters out manipulated penny
stocks, illiquid names, and just-listed IPOs that pattern detectors would
otherwise produce noise on.

Inputs the filter expects (any may be None — missing fields default-pass
with a warning rather than blocking the pipeline):

    market_cap_cr        : INR crores (1 cr = 10^7 INR)
    listing_date         : pd.Timestamp of first trading day
    promoter_pledge_pct  : 0-100, percentage of promoter holdings pledged
                           [Phase 2 will wire this up; Phase 1 defaults pass.]

Average daily turnover (ADV) is computed from the price/volume series
directly, so it never relies on external metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from breakout.config import Thresholds


# Number of trading days used to compute average daily turnover.
_ADV_LOOKBACK_BARS = 20
# 1 INR crore = 10^7 INR
_CRORE = 10_000_000


@dataclass
class QualityResult:
    """Outcome of the quality gate.

    `reasons_failed` is empty iff `passed` is True. Each entry describes
    a single failed check — useful for logging which filter knocked out a
    given symbol and tuning thresholds later.
    """

    passed: bool
    reasons_failed: list[str] = field(default_factory=list)
    # Computed values that downstream layers may want to surface in alerts.
    adv_cr: float = 0.0
    market_cap_cr: float | None = None


def check_quality(
    df: pd.DataFrame,
    metadata: dict | None,
    thresholds: Thresholds,
) -> QualityResult:
    """Apply the quality floor.

    Args:
        df: OHLCV DataFrame (lowercase columns) for the symbol.
        metadata: Optional dict with market_cap_cr, listing_date,
            promoter_pledge_pct. Pass None or omit individual keys when
            unknown — those checks default-pass with a soft note.
        thresholds: Configured min_market_cap_cr, min_adv_cr, etc.

    Returns:
        QualityResult with .passed and the failure reasons.
    """
    reasons: list[str] = []
    metadata = metadata or {}

    # ── ADV (computed from data) ────────────────────────────────────────
    adv_cr = _compute_adv_in_crores(df)
    if adv_cr < thresholds.min_adv_cr:
        reasons.append(
            f"adv_below_floor:{adv_cr:.2f}cr<{thresholds.min_adv_cr}cr"
        )

    # ── Market cap (from metadata, default-pass if absent) ───────────────
    market_cap_cr = metadata.get("market_cap_cr")
    if market_cap_cr is not None and market_cap_cr < thresholds.min_market_cap_cr:
        reasons.append(
            f"market_cap_below_floor:{market_cap_cr:.0f}cr<{thresholds.min_market_cap_cr}cr"
        )

    # ── Listing age (from metadata) ──────────────────────────────────────
    listing_date = metadata.get("listing_date")
    if listing_date is not None:
        if isinstance(listing_date, str):
            listing_date = pd.to_datetime(listing_date)
        days_listed = (datetime.now() - listing_date.to_pydatetime()).days
        if days_listed < thresholds.min_listing_days:
            reasons.append(
                f"too_recent_listing:{days_listed}d<{thresholds.min_listing_days}d"
            )

    # ── Promoter pledge (Phase 2 will wire the source) ───────────────────
    # When the source isn't connected yet, we cannot enforce the < 30% rule.
    # Documented as a known deferral in MEMORY/project_overview.
    pledge_pct = metadata.get("promoter_pledge_pct")
    if pledge_pct is not None and pledge_pct >= 30.0:
        reasons.append(f"high_promoter_pledge:{pledge_pct:.1f}%")

    return QualityResult(
        passed=not reasons,
        reasons_failed=reasons,
        adv_cr=adv_cr,
        market_cap_cr=market_cap_cr,
    )


def _compute_adv_in_crores(df: pd.DataFrame) -> float:
    """Average daily turnover over the last 20 trading days, in INR crores."""
    if len(df) < _ADV_LOOKBACK_BARS or "volume" not in df.columns:
        return 0.0
    recent = df.iloc[-_ADV_LOOKBACK_BARS:]
    turnover = (recent["close"] * recent["volume"]).mean()
    return float(turnover) / _CRORE
