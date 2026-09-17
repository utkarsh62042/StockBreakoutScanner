"""The chase penalty — extension past the pivot.

By the 3 PM confirmation a breakout has usually already run some distance past
its level, and how far matters: the stop is anchored to the level, so a wider
extension means wider risk per share, a smaller position, and less of the
measured move left to capture. Minervini's rule of thumb is roughly "don't
chase more than ~5% past the pivot".

`tracker.py` has always computed `extension_pct` and `alert_features` logs it,
but nothing acted on it until now.
"""

from __future__ import annotations

import pytest

from breakout.analysis.patterns import PatternMatch
from breakout.analysis.stage import Stage
from breakout.jobs.preclose_scan import _rescore
from breakout.scoring import ScoringFeatures, composite_score, extension_penalty

FREE, MAX, PENALTY = 2.0, 8.0, 10.0


def _pen(ext) -> float:
    return extension_penalty(ext, FREE, MAX, PENALTY)


# ── The penalty curve ────────────────────────────────────────────────────


def test_a_tight_entry_is_not_penalised() -> None:
    """Ordinary noise above the level isn't chasing."""
    assert _pen(0.0) == 0.0
    assert _pen(0.5) == 0.0
    assert _pen(FREE) == 0.0


def test_the_penalty_ramps_between_the_free_band_and_the_cap() -> None:
    assert _pen(5.0) == pytest.approx(5.0)      # midpoint of 2%..8%
    assert _pen(MAX) == pytest.approx(PENALTY)


def test_the_penalty_is_monotonic() -> None:
    vals = [_pen(e) for e in (0, 1, 2, 3, 4, 5, 6, 7, 8)]
    assert vals == sorted(vals)


def test_the_penalty_is_capped() -> None:
    """A 40% extension is absurd, but it shouldn't be able to swamp the score."""
    assert _pen(40.0) == pytest.approx(PENALTY)
    assert _pen(400.0) == pytest.approx(PENALTY)


def test_entering_below_the_level_is_not_a_chase() -> None:
    """The pullback path enters at or under the level — never penalise it."""
    assert _pen(-3.0) == 0.0


def test_unknown_extension_is_never_penalised() -> None:
    """The morning scan has no entry yet; absence of data is not evidence of
    a chase."""
    assert _pen(None) == 0.0
    assert _pen(float("nan")) == 0.0


def test_degenerate_band_does_not_divide_by_zero() -> None:
    assert extension_penalty(5.0, free_pct=8.0, max_pct=8.0) == 0.0
    assert extension_penalty(9.0, free_pct=8.0, max_pct=8.0, max_penalty=10.0) == 10.0


# ── Effect on the composite ──────────────────────────────────────────────


def _score(ext) -> float:
    return composite_score(
        ScoringFeatures(
            quality_pass=True, stage=Stage.STAGE_2, earnings_blackout=False,
            pattern_match=PatternMatch(
                detected=True, pattern_name="nr7", confidence=60.0,
                breakout_level=100.0, base_start_idx=0, base_end_idx=0,
                base_height=5.0, notes={},
            ),
            volume_ratio=2.0, extension_pct=ext,
        ),
        extension_free_pct=FREE, extension_max_pct=MAX,
        extension_max_penalty=PENALTY,
    )


def test_chasing_costs_score() -> None:
    assert _score(6.0) < _score(1.0)


def test_an_unknown_extension_scores_like_a_tight_one() -> None:
    """So the morning watchlist score stays comparable to what it was."""
    assert _score(None) == pytest.approx(_score(0.0))


def test_the_penalty_cannot_drive_the_score_negative() -> None:
    """0 means "failed a hard gate". A chased entry is weak, not disqualified."""
    worst = composite_score(
        ScoringFeatures(
            quality_pass=True, stage=Stage.STAGE_2, earnings_blackout=False,
            pattern_match=PatternMatch(
                detected=True, pattern_name="nr7", confidence=0.0,
                breakout_level=100.0, base_start_idx=0, base_end_idx=0,
                base_height=0.0, notes={},
            ),
            volume_ratio=0.0, sector_trend="down", extension_pct=50.0,
        ),
        extension_free_pct=FREE, extension_max_pct=MAX,
        extension_max_penalty=100.0,
    )
    assert worst == 0.0


# ── Through the pre-close rescore, which is where it actually bites ──────


def _row() -> dict:
    return {
        "pattern": "nr7", "pattern_confidence": 60.0, "breakout_level": 100.0,
        "base_height": 5.0, "rs_points": 0.0, "tightness": 0.0,
        "sector_trend": "flat", "score": 50.0,
    }


def test_rescore_docks_a_chased_entry() -> None:
    tight = _rescore(_row(), vol_ratio=2.0, entry_price=101.0)
    chased = _rescore(_row(), vol_ratio=2.0, entry_price=107.0)
    assert chased < tight


def test_rescore_without_an_entry_price_applies_no_penalty() -> None:
    """Back-compat: callers that don't pass an entry still get the old score."""
    assert _rescore(_row(), vol_ratio=2.0) == pytest.approx(
        _rescore(_row(), vol_ratio=2.0, entry_price=100.0)
    )
