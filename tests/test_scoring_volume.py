"""The volume component of the composite score.

Volume confirmation is arguably the second-strongest signal the scanner has,
and until now it contributed nothing to the ranking: the scale
`min(15, volume_ratio / 1.5 * 15)` saturated at exactly the 1.5x confirmation
gate, so every alert scored the full 15 points and a 3x breakout ranked no
higher than a 1.5x one.
"""

from __future__ import annotations

import pytest

from breakout.analysis.patterns import PatternMatch
from breakout.analysis.stage import Stage
from breakout.scoring import ScoringFeatures, composite_score, volume_points

WEIGHT = 15.0
GATE = 1.5
SAT = 3.0


def _pts(vr: float) -> float:
    return volume_points(vr, WEIGHT, GATE, SAT)


# ── The scale ────────────────────────────────────────────────────────────


def test_the_gate_earns_half_the_weight() -> None:
    """Not zero: clearing the confirmation gate is itself evidence. Not full:
    that was the bug."""
    assert _pts(GATE) == pytest.approx(WEIGHT / 2)


def test_saturation_earns_the_full_weight() -> None:
    assert _pts(SAT) == pytest.approx(WEIGHT)


def test_a_3x_breakout_outranks_a_1point5x_one() -> None:
    """The whole point of the change."""
    assert _pts(3.0) > _pts(1.5)


def test_the_scale_is_monotonic() -> None:
    vals = [_pts(v) for v in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)]
    assert vals == sorted(vals)


def test_the_scale_is_continuous_at_the_knee() -> None:
    """A candidate crossing the gate must not lurch in the ranking."""
    assert _pts(1.49) == pytest.approx(_pts(1.5), abs=0.1)


def test_below_the_gate_still_discriminates() -> None:
    """The morning watchlist holds candidates that haven't broken out yet, so
    sub-gate volume has to rank rather than collapse to a single value."""
    assert 0.0 < _pts(0.5) < _pts(1.0) < _pts(1.5)
    assert _pts(0.0) == pytest.approx(0.0)


def test_points_are_capped_past_saturation() -> None:
    """A 10x spike is usually news, not accumulation."""
    assert _pts(10.0) == pytest.approx(WEIGHT)
    assert _pts(100.0) == pytest.approx(WEIGHT)


def test_negative_volume_ratio_is_floored_not_negative() -> None:
    """A bad feed must not hand out negative points and drag a score below 0."""
    assert _pts(-5.0) == pytest.approx(0.0)


def test_degenerate_config_does_not_divide_by_zero() -> None:
    assert volume_points(2.0, WEIGHT, min_ratio=0.0) == pytest.approx(WEIGHT)
    assert volume_points(2.0, WEIGHT, min_ratio=1.5, saturation_ratio=1.5) == WEIGHT


# ── Effect on the composite ──────────────────────────────────────────────


def _score(vr: float) -> float:
    return composite_score(
        ScoringFeatures(
            quality_pass=True,
            stage=Stage.STAGE_2,
            earnings_blackout=False,
            pattern_match=PatternMatch(
                detected=True, pattern_name="nr7", confidence=60.0,
                breakout_level=100.0, base_start_idx=0, base_end_idx=0,
                base_height=5.0, notes={},
            ),
            volume_ratio=vr,
        ),
        min_volume_ratio=GATE,
        volume_saturation_ratio=SAT,
    )


def test_volume_now_moves_the_composite() -> None:
    """Previously both of these scored identically."""
    assert _score(3.0) - _score(1.5) == pytest.approx(7.5)


def test_the_widened_scale_costs_a_marginal_alert_points() -> None:
    """Stated plainly because it changes how many alerts fire: a breakout that
    just clears the gate now scores 7.5 lower than it used to, when volume was
    a free 15 points for everyone."""
    assert _score(1.5) == pytest.approx(_score(3.0) - 7.5)


def test_saturation_ratio_is_configurable() -> None:
    """Widening it further makes the same breakout score lower — the knob does
    what it says."""
    wide = composite_score(
        ScoringFeatures(
            quality_pass=True, stage=Stage.STAGE_2, earnings_blackout=False,
            pattern_match=PatternMatch(
                detected=True, pattern_name="nr7", confidence=60.0,
                breakout_level=100.0, base_start_idx=0, base_end_idx=0,
                base_height=5.0, notes={},
            ),
            volume_ratio=3.0,
        ),
        min_volume_ratio=GATE,
        volume_saturation_ratio=6.0,
    )
    assert wide < _score(3.0)
