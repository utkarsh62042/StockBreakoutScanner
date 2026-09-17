"""Scoring the close within the breakout bar's range.

The GUIDE's thesis is that institutional commitment shows in the close. The
confirmation gate is `close > level`, which treats a breakout that closed on
its high identically to one that spent the day at the level and closed near its
low having given back everything it gained. Those are not the same event, and
until now the score could not tell them apart.

The 5 points come out of the stage component, which was a constant 20 for every
scored candidate — a hard gate carries no information once passed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from breakout.analysis.patterns import PatternMatch
from breakout.analysis.stage import Stage
from breakout.jobs.preclose_scan import _rescore
from breakout.scoring import (
    CLOSE_IN_RANGE_WEIGHT,
    STAGE_WEIGHT,
    ScoringFeatures,
    close_in_range_points,
    composite_score,
)


# ── The component ────────────────────────────────────────────────────────


def test_closing_on_the_high_earns_full_weight() -> None:
    assert close_in_range_points(1.0) == pytest.approx(CLOSE_IN_RANGE_WEIGHT)


def test_closing_on_the_low_earns_nothing() -> None:
    assert close_in_range_points(0.0) == 0.0


def test_it_scales_linearly_between() -> None:
    assert close_in_range_points(0.5) == pytest.approx(CLOSE_IN_RANGE_WEIGHT / 2)


def test_unknown_earns_full_weight_not_zero() -> None:
    """The morning scan has no breakout bar to judge. Scoring it zero — or a
    midpoint — would shift every watchlist score for no informational reason."""
    assert close_in_range_points(None) == pytest.approx(CLOSE_IN_RANGE_WEIGHT)
    assert close_in_range_points(float("nan")) == pytest.approx(CLOSE_IN_RANGE_WEIGHT)


def test_out_of_range_values_are_clamped() -> None:
    """close_in_range is a ratio in [0, 1]; a bad bar must not leak points."""
    assert close_in_range_points(5.0) == pytest.approx(CLOSE_IN_RANGE_WEIGHT)
    assert close_in_range_points(-2.0) == 0.0


# ── The weight came out of the dead stage constant ───────────────────────


def test_the_budget_still_tops_out_at_100() -> None:
    assert STAGE_WEIGHT + CLOSE_IN_RANGE_WEIGHT == 20.0
    assert 35 + STAGE_WEIGHT + 15 + 15 + 10 + CLOSE_IN_RANGE_WEIGHT + 5 == 100


def _score(cir) -> float:
    return composite_score(
        ScoringFeatures(
            quality_pass=True, stage=Stage.STAGE_2, earnings_blackout=False,
            pattern_match=PatternMatch(
                detected=True, pattern_name="nr7", confidence=60.0,
                breakout_level=100.0, base_start_idx=0, base_end_idx=0,
                base_height=5.0, notes={},
            ),
            volume_ratio=2.0, close_in_range=cir,
        )
    )


def test_a_strong_close_scores_what_it_used_to() -> None:
    """Stage 20 became stage 15 + close 5, so nothing is lost at the top —
    only weak closes give ground."""
    assert _score(1.0) == pytest.approx(_score(None))


def test_a_weak_close_now_costs_the_full_five_points() -> None:
    assert _score(1.0) - _score(0.0) == pytest.approx(CLOSE_IN_RANGE_WEIGHT)


def test_the_close_orders_otherwise_identical_setups() -> None:
    assert _score(0.0) < _score(0.5) < _score(1.0)


# ── Through the pre-close rescore ────────────────────────────────────────


def _row() -> dict:
    return {
        "pattern": "nr7", "pattern_confidence": 60.0, "breakout_level": 100.0,
        "base_height": 5.0, "rs_points": 0.0, "tightness": 0.0,
        "sector_trend": "flat", "score": 50.0,
    }


def _bar(high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": low, "high": high, "low": low, "close": close})


def test_rescore_rewards_the_bar_that_closed_on_its_high() -> None:
    strong = _rescore(_row(), 2.0, entry_price=101.0, bar=_bar(101.0, 99.0, 101.0))
    weak = _rescore(_row(), 2.0, entry_price=101.0, bar=_bar(105.0, 100.9, 101.0))
    assert strong > weak


def test_rescore_without_a_bar_is_unchanged() -> None:
    """Back-compat: the old two-argument call still scores as it did."""
    assert _rescore(_row(), 2.0) == pytest.approx(
        _rescore(_row(), 2.0, bar=_bar(101.0, 99.0, 101.0))
    )
