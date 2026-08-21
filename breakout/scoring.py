"""Composite scoring.

Combines hard pass/fail filters and weighted signal contributions into a
single 0–100 score. The four hard gates short-circuit to 0 — if any fail,
the stock is excluded regardless of how strong its other signals look.

Phase 1 only populates pattern + stage + volume + sector (with sector
defaulting to 'flat' until the sector module exists). RS and tightness
land in Phase 2; their default values keep the score formula well-defined
without depending on stub data.
"""

from __future__ import annotations

from dataclasses import dataclass

from breakout.analysis.patterns import PatternMatch
from breakout.analysis.stage import Stage


@dataclass
class ScoringFeatures:
    """Inputs to the composite scorer.

    Mandatory fields are the four hard-gate signals. Optional fields default
    to safe values so a Phase 1 call can omit them; Phase 2 will populate
    them with real data.
    """

    quality_pass: bool
    stage: Stage
    earnings_blackout: bool
    pattern_match: PatternMatch | None
    volume_ratio: float = 1.0      # last bar vs 20-day avg
    rs_score: float = 0.0          # 0-15 percentile score vs NIFTY 500
    tightness_score: float = 0.0   # 0-1 normalized ATR contraction
    sector_trend: str = "flat"      # "up" | "flat" | "down"


def composite_score(features: ScoringFeatures) -> float:
    """Score a candidate from 0 to 100.

    Returns 0 immediately if any hard gate fails (quality, stage, earnings
    blackout, no pattern match). Otherwise sums the weighted components:

        pattern * 0.35  (max 35)
      + stage_2_flat    (max 20)
      + rs_score        (max 15)
      + volume          (max 15)
      + tightness       (max 10)
      + sector          (max 5)
      = composite       (max 100)
    """
    # Hard gates
    if not features.quality_pass:
        return 0.0
    if features.stage != Stage.STAGE_2:
        return 0.0
    if features.earnings_blackout:
        return 0.0
    if features.pattern_match is None or not features.pattern_match.detected:
        return 0.0

    pattern_pts = features.pattern_match.confidence * 0.35
    stage_pts = 20.0  # guaranteed by gate above
    rs_pts = min(15.0, max(0.0, features.rs_score))
    volume_pts = min(15.0, (features.volume_ratio / 1.5) * 15.0)
    tightness_pts = min(10.0, max(0.0, features.tightness_score * 10.0))
    sector_pts = {"up": 5.0, "flat": 3.0, "down": 0.0}.get(features.sector_trend, 3.0)

    return pattern_pts + stage_pts + rs_pts + volume_pts + tightness_pts + sector_pts
