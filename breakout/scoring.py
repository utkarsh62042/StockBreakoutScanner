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


#: Volume-ratio scale. `min_ratio` is the confirmation gate (config
#: `thresholds.min_volume_ratio`); `saturation_ratio` is where the volume
#: component maxes out.
#:
#: The old scale was `min(15, volume_ratio / 1.5 * 15)` — it hit the cap at
#: exactly the gate, so every alert scored the full 15 and a 3× breakout ranked
#: identically to a 1.5× one. Arguably the second-strongest signal in the system
#: contributed *nothing* to the ranking.
#:
#: The scale is now piecewise-linear with a knee at the gate:
#:
#:      0.0×  ->  0 pts        below the gate: still ranked, for the morning
#:      1.5×  ->  7.5 pts      watchlist, where candidates haven't broken out yet
#:      3.0×  -> 15 pts        and beyond: capped
#:
#: Half weight at the gate rather than zero, deliberately: clearing the
#: confirmation gate is itself evidence, and starting at zero would say a 1.5×
#: breakout has no volume support at all. Continuous at the knee, so the ranking
#: doesn't jump as a candidate crosses it.
_VOLUME_KNEE_FRACTION = 0.5
DEFAULT_MIN_VOLUME_RATIO = 1.5
DEFAULT_VOLUME_SATURATION_RATIO = 3.0


def volume_points(
    volume_ratio: float,
    weight: float = 15.0,
    min_ratio: float = DEFAULT_MIN_VOLUME_RATIO,
    saturation_ratio: float = DEFAULT_VOLUME_SATURATION_RATIO,
) -> float:
    """Points for a breakout's volume confirmation. See the scale above."""
    vr = max(0.0, volume_ratio)
    knee = weight * _VOLUME_KNEE_FRACTION
    if min_ratio <= 0:
        return weight
    if vr <= min_ratio:
        return vr / min_ratio * knee
    span = saturation_ratio - min_ratio
    if span <= 0:
        return weight
    return min(weight, knee + (vr - min_ratio) / span * (weight - knee))


#: Chase penalty. `extension_pct` is how far above the breakout level the entry
#: actually prints — at the 3 PM confirmation the move has usually already run
#: some distance past the pivot, and how far matters:
#:
#:   * the stop is anchored to the level, so a wider extension means a wider
#:     risk per share and therefore a smaller position for the same rupee risk
#:   * `target_2` is a measured move from the level, so extension eats directly
#:     into the reachable upside
#:   * empirically (Minervini) chasing more than ~5% past a pivot fails more often
#:
#: The scale: free up to `free_pct` (ordinary noise above the level), then a
#: linear ramp to the full penalty at `max_pct`, capped beyond.
#:
#:      <= 2%  ->   0 pts off
#:         5%  ->  -5 pts
#:      >= 8%  -> -10 pts
#:
#: A penalty rather than a hard gate, for the same reason the regime gate sizes
#: down rather than blocking (see `filters.regime`): a rejected alert leaves no
#: `alert_features` row, so a hard cut would destroy the evidence needed to find
#: the real cut point. In practice the penalty plus `min_score_to_alert` acts as
#: a soft gate — a badly extended entry simply stops clearing the bar.
DEFAULT_EXTENSION_FREE_PCT = 2.0
DEFAULT_EXTENSION_MAX_PCT = 8.0
DEFAULT_EXTENSION_MAX_PENALTY = 10.0


def extension_penalty(
    extension_pct: float | None,
    free_pct: float = DEFAULT_EXTENSION_FREE_PCT,
    max_pct: float = DEFAULT_EXTENSION_MAX_PCT,
    max_penalty: float = DEFAULT_EXTENSION_MAX_PENALTY,
) -> float:
    """Points to subtract for chasing a breakout. Always >= 0. See the scale above.

    `None` means "not known at this point in the pipeline" — the morning scan
    scores candidates that haven't broken out yet, and there is no entry to
    measure. Unknown is never penalised.
    """
    if extension_pct is None or extension_pct != extension_pct:   # None / NaN
        return 0.0
    ext = float(extension_pct)
    # Entering *below* the level isn't a chase; it's the pullback path.
    if ext <= free_pct:
        return 0.0
    span = max_pct - free_pct
    if span <= 0:
        return max_penalty
    return min(max_penalty, (ext - free_pct) / span * max_penalty)


#: Close-in-range: where the bar closed within its own high-low range.
#: 0 = closed at the day's low, 1 = at the high.
#:
#: This is the direct measurement of the thesis the GUIDE argues for —
#: *institutional commitment shows in the close*. The confirmation gate is
#: `close > level`, which treats a breakout that closed on its high identically
#: to one that spent the day at the level and closed near its low, having given
#: back everything it gained. Those are not the same event.
#:
#: The 5 points come out of the **stage** component, which was a constant 20 for
#: every scored candidate (stage is a hard gate, so passing it carries no
#: information — a documented dead weight). Trading 5 points of pure offset for
#: 5 points of real signal leaves the maximum at 100 and leaves a strong close
#: scoring exactly what it did before; only a weak close now gives ground.
STAGE_WEIGHT = 15.0
CLOSE_IN_RANGE_WEIGHT = 5.0


def close_in_range_points(
    close_in_range: float | None, weight: float = CLOSE_IN_RANGE_WEIGHT
) -> float:
    """Points for closing strong within the bar's range.

    `None` — the morning scan, which has no breakout bar to judge — earns the
    full weight rather than zero or a midpoint. Anything else would shift every
    watchlist score for no informational reason; the discrimination belongs at
    the pre-close rescore, where there is an actual breakout bar to read.
    """
    if close_in_range is None or close_in_range != close_in_range:   # None / NaN
        return weight
    return min(weight, max(0.0, float(close_in_range) * weight))


def scoring_params(thresholds) -> dict:
    """The tunable scoring knobs from a `Thresholds` config, as kwargs for
    `composite_score`. Keeps the three call sites (morning scan, pre-close
    rescore, backtest) from drifting apart as knobs are added."""
    return {
        "min_volume_ratio": thresholds.min_volume_ratio,
        "volume_saturation_ratio": thresholds.volume_saturation_ratio,
        "extension_free_pct": thresholds.extension_free_pct,
        "extension_max_pct": thresholds.extension_max_pct,
        "extension_max_penalty": thresholds.extension_max_penalty,
    }


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
    #: % the entry prints above the breakout level. None when there is no entry
    #: yet (the morning scan) — never penalised. See `extension_penalty`.
    extension_pct: float | None = None
    #: Where the confirmation bar closed in its own range (0 = low, 1 = high).
    #: None when there is no breakout bar yet. See `close_in_range_points`.
    close_in_range: float | None = None


def composite_score(
    features: ScoringFeatures,
    min_volume_ratio: float = DEFAULT_MIN_VOLUME_RATIO,
    volume_saturation_ratio: float = DEFAULT_VOLUME_SATURATION_RATIO,
    extension_free_pct: float = DEFAULT_EXTENSION_FREE_PCT,
    extension_max_pct: float = DEFAULT_EXTENSION_MAX_PCT,
    extension_max_penalty: float = DEFAULT_EXTENSION_MAX_PENALTY,
) -> float:
    """Score a candidate from 0 to 100.

    Returns 0 immediately if any hard gate fails (quality, stage, earnings
    blackout, no pattern match). Otherwise sums the weighted components:

        pattern * 0.35  (max 35)
      + stage_2_flat    (max 15)
      + rs_score        (max 15)
      + volume          (max 15)
      + tightness       (max 10)
      + close_in_range  (max 5)
      + sector          (max 5)
      - extension       (max 10 off — the chase penalty)
      = composite       (0..100)
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
    stage_pts = STAGE_WEIGHT  # guaranteed by gate above — a constant, so it
                              # discriminates nothing; 5 of its old 20 points
                              # were moved to close_in_range below.
    rs_pts = min(15.0, max(0.0, features.rs_score))
    volume_pts = volume_points(
        features.volume_ratio,
        weight=15.0,
        min_ratio=min_volume_ratio,
        saturation_ratio=volume_saturation_ratio,
    )
    tightness_pts = min(10.0, max(0.0, features.tightness_score * 10.0))
    close_pts = close_in_range_points(features.close_in_range)
    sector_pts = {"up": 5.0, "flat": 3.0, "down": 0.0}.get(features.sector_trend, 3.0)

    chase_penalty = extension_penalty(
        features.extension_pct,
        free_pct=extension_free_pct,
        max_pct=extension_max_pct,
        max_penalty=extension_max_penalty,
    )

    total = (
        pattern_pts + stage_pts + rs_pts + volume_pts + tightness_pts
        + close_pts + sector_pts
        - chase_penalty
    )
    # A penalty must never push the score negative: 0 is reserved for "failed a
    # hard gate", and a chased entry is a weak setup, not a disqualified one.
    return max(0.0, total)
