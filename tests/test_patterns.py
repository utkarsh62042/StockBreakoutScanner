"""Pattern detector tests.

Per the build spec: each detector needs at least 3 positive and 3 negative
fixtures. Fixtures are hand-crafted so we can reason about exactly what's
being detected and why, rather than checking against arbitrary numbers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breakout.analysis.patterns import (
    PATTERN_REGISTRY,
    PatternMatch,
    detect_all,
    detect_ascending_triangle,
    detect_bollinger_squeeze,
    detect_cup_and_handle,
    detect_darvas_box,
    detect_fifty_two_week_high_breakout,
    detect_flag,
    detect_inside_bar_breakout,
    detect_nr7,
    detect_vcp,
)


def _zigzag_path(extrema: list[float], leg: int = 7) -> list[float]:
    """Build a price path through the given alternating extrema, with each
    extremum landing on a single bar (strict local max/min) so find_pivots
    locks onto it. Extrema fall at indices 0, leg, 2*leg, ..."""
    path = [extrema[0]]
    for i in range(1, len(extrema)):
        path.extend(list(np.linspace(extrema[i - 1], extrema[i], leg + 1))[1:])
    return path


def _make_ohlcv(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    """Build an OHLCV DataFrame. When `highs` or `lows` is None we use
    close ± 0.5% so the bar has a sensible range without affecting tests."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if volumes is None:
        volumes = [100000.0] * n
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


# ╭──────────────────────────────────────────────────────────────────────╮
# │ 52-week high breakout                                                │
# ╰──────────────────────────────────────────────────────────────────────╯


def test_52w_high_clean_uptrend_near_high() -> None:
    """Linear uptrend over 252 days, today's close 1% below the 52w high."""
    closes = list(np.linspace(100, 200, 252))
    # Drop today's close to be 99% of the running max (which is closes[-1] ≈ 200)
    closes[-1] = closes[-2] * 0.99
    df = _make_ohlcv(closes)
    result = detect_fifty_two_week_high_breakout(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(max(c * 1.005 for c in closes))
    assert result.confidence >= 50


def test_52w_high_with_multiple_touches_has_higher_confidence() -> None:
    """Several approaches to the high without breaking gives higher confidence."""
    # Sparse fixture: smooth trend up, just the last bar near the 52w high
    sparse_closes = list(np.linspace(100, 195, 251)) + [199.0]
    df_sparse = _make_ohlcv(sparse_closes)

    # Crowded fixture: many bars bump against the same resistance
    base = list(np.linspace(100, 196, 200))
    touch_cycle = [199.0, 197.5, 199.5, 198.0, 199.8, 197.0, 198.5, 199.7]
    crowded_closes = base + (touch_cycle * 7)[:50] + [199.5, 199.0, 198.5]
    assert len(crowded_closes) == 253
    df_crowded = _make_ohlcv(crowded_closes)

    sparse_result = detect_fifty_two_week_high_breakout(df_sparse)
    crowded_result = detect_fifty_two_week_high_breakout(df_crowded)

    assert sparse_result.detected
    assert crowded_result.detected
    assert crowded_result.confidence > sparse_result.confidence
    assert crowded_result.notes["touches_in_6mo"] > sparse_result.notes["touches_in_6mo"]


def test_52w_high_exactly_at_high_detected() -> None:
    """Distance 0% (close == 52w high) is the strongest setup case."""
    closes = list(np.linspace(100, 199, 251)) + [199.0]
    df = _make_ohlcv(closes)
    result = detect_fifty_two_week_high_breakout(df)
    assert result.detected
    # distance_pct should be near zero
    assert result.notes["distance_pct_from_high"] < 0.005


def test_52w_high_too_far_below_not_detected() -> None:
    """Close 10% below 52w high → outside the setup window."""
    closes = list(np.linspace(100, 200, 252))
    closes[-1] = 175.0  # 12% below the running max
    df = _make_ohlcv(closes)
    result = detect_fifty_two_week_high_breakout(df)
    assert not result.detected


def test_52w_high_insufficient_history_not_detected() -> None:
    closes = list(np.linspace(100, 200, 100))  # only 100 bars
    df = _make_ohlcv(closes)
    result = detect_fifty_two_week_high_breakout(df)
    assert not result.detected


def test_52w_high_old_high_recent_correction_not_detected() -> None:
    """52w high was set early; price has been falling for months."""
    early = list(np.linspace(100, 200, 50))   # rises to 200
    decline = list(np.linspace(200, 140, 202))  # falls to 140
    closes = early + decline
    assert len(closes) == 252
    df = _make_ohlcv(closes)
    result = detect_fifty_two_week_high_breakout(df)
    assert not result.detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Darvas Box                                                           │
# ╰──────────────────────────────────────────────────────────────────────╯


def _build_darvas_fixture(
    pre_box_bars: int,
    box_bars: int,
    ceiling: float,
    floor: float,
    touch_indices: list[int],
) -> pd.DataFrame:
    """Construct a clean Darvas box with explicit ceiling touches.

    Non-touch bars cycle through three (high, low) pairs whose highs stay
    clearly below the 1% ceiling-touch band and whose lows sit above the floor,
    so `touch_indices` is the authoritative touch count. Touch bars print
    exactly at the ceiling.
    """
    # Three non-touch (high, low) pairs as percentages of ceiling/floor.
    # Highs max out at 98.8% of the ceiling — below the touch threshold
    # (99% of ceiling) — so they never inflate the touch count. Lows stay
    # above the floor.
    high_pct = [0.975, 0.982, 0.988]
    low_pct = [1.005, 1.010, 1.015]

    pre = list(np.linspace(floor * 0.85, floor * 1.005, pre_box_bars))
    pre_highs = [c * 1.005 for c in pre]
    pre_lows = [c * 0.995 for c in pre]

    box_highs: list[float] = []
    box_lows: list[float] = []
    box_closes: list[float] = []
    for i in range(box_bars):
        if i in touch_indices:
            high = ceiling
            low = floor + (ceiling - floor) * 0.3
        else:
            cycle = i % 3
            high = ceiling * high_pct[cycle]
            low = floor * low_pct[cycle]
            # Sanity: a non-touch high should never accidentally equal ceiling
            if low >= high:
                low = high - 0.01

        close = (high + low) / 2
        box_highs.append(high)
        box_lows.append(low)
        box_closes.append(close)

    # Anchor the floor (so min(low) == floor exactly).
    box_lows[box_bars // 2] = floor

    closes = pre + box_closes
    highs = pre_highs + box_highs
    lows = pre_lows + box_lows
    return _make_ohlcv(closes, highs, lows)


def test_darvas_basic_25_day_box_with_4_touches_detected() -> None:
    df = _build_darvas_fixture(
        pre_box_bars=30,
        box_bars=25,
        ceiling=100.0,
        floor=96.0,  # 4% box height
        touch_indices=[0, 6, 12, 24],
    )
    result = detect_darvas_box(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(100.0)
    assert result.base_height == pytest.approx(4.0)
    assert result.notes["touches"] >= 4


def test_darvas_30_day_box_at_minimum_touches_detected() -> None:
    df = _build_darvas_fixture(
        pre_box_bars=20,
        box_bars=30,
        ceiling=200.0,
        floor=193.0,  # 3.5% box height
        touch_indices=[0, 15, 29],
    )
    result = detect_darvas_box(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(200.0)


def test_darvas_50_day_tight_box_gets_tightness_bonus() -> None:
    """A box with rel_width < 8% should pick up the tightness confidence bonus."""
    df_loose = _build_darvas_fixture(
        pre_box_bars=10,
        box_bars=25,
        ceiling=100.0,
        floor=92.0,  # 8% wide → no tightness bonus
        touch_indices=[0, 10, 20, 24],
    )
    df_tight = _build_darvas_fixture(
        pre_box_bars=10,
        box_bars=25,
        ceiling=100.0,
        floor=95.0,  # 5% wide → tightness bonus applies
        touch_indices=[0, 10, 20, 24],
    )
    loose = detect_darvas_box(df_loose)
    tight = detect_darvas_box(df_tight)
    assert tight.detected
    assert loose.detected
    assert tight.confidence > loose.confidence


def test_darvas_trending_series_not_detected() -> None:
    closes = list(np.linspace(100, 150, 60))
    df = _make_ohlcv(closes)
    result = detect_darvas_box(df)
    assert not result.detected


def test_darvas_only_2_touches_not_detected() -> None:
    df = _build_darvas_fixture(
        pre_box_bars=10,
        box_bars=25,
        ceiling=100.0,
        floor=96.0,
        touch_indices=[0, 24],  # only 2 touches
    )
    result = detect_darvas_box(df)
    assert not result.detected


def test_darvas_too_wide_top_not_detected() -> None:
    """A 'box' whose highs span more than 3% under the ceiling fails."""
    # Construct: 25 bars, ceiling 100, but most highs hover around 94 (6% below)
    n_box = 25
    box_highs = [100.0] + [94.0] * (n_box - 2) + [100.0]
    box_lows = [93.0] * n_box
    box_closes = [(h + l) / 2 for h, l in zip(box_highs, box_lows)]
    pre_closes = list(np.linspace(80, 92, 30))
    df = _make_ohlcv(
        closes=pre_closes + box_closes,
        highs=[c * 1.005 for c in pre_closes] + box_highs,
        lows=[c * 0.995 for c in pre_closes] + box_lows,
    )
    result = detect_darvas_box(df)
    assert not result.detected


def test_darvas_insufficient_bars_not_detected() -> None:
    closes = [100.0] * 15  # too few
    df = _make_ohlcv(closes)
    result = detect_darvas_box(df)
    assert not result.detected


# ── Corrected-interpretation tests ───────────────────────────────────────
# The detector defines a box by its ceiling/floor boundaries, NOT by requiring
# every daily high to hug the ceiling. These fixtures lock in that behaviour.


def test_darvas_box_with_deep_pullback_bar_detected() -> None:
    """A box with a normal deep pullback bar (its HIGH well below the ceiling)
    is still a valid box. The old 'every high within 3% of ceiling' rule
    wrongly rejected these — the single reason it matched only 2/500 stocks."""
    n = 25
    highs = [97.0] * n
    lows = [95.0] * n
    highs[0] = highs[18] = highs[24] = 100.0   # 3 ceiling touches, retested late
    highs[12], lows[12] = 92.0, 90.0           # deep pullback: high 8% below ceiling, sets floor
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = _make_ohlcv(closes, highs, lows)
    result = detect_darvas_box(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(100.0)
    assert result.notes["floor"] == pytest.approx(90.0)
    assert result.notes["touches"] >= 3


def test_darvas_runup_not_absorbed_into_box() -> None:
    """A preceding run-up must not be swallowed into the box: the box opens on
    the ceiling touch, so base_start_idx is the box's left edge, not the ramp."""
    df = _build_darvas_fixture(
        pre_box_bars=30,
        box_bars=25,
        ceiling=100.0,
        floor=96.0,
        touch_indices=[0, 10, 24],
    )
    result = detect_darvas_box(df)
    assert result.detected
    assert result.base_start_idx == 30            # right after the 30-bar ramp
    assert result.base_height == pytest.approx(4.0)


def test_darvas_box_too_tall_not_detected() -> None:
    """Opens on a touch with 3 touches, but 25% tall — a trending range, not
    a consolidation box."""
    n = 25
    highs = [90.0] * n
    lows = [78.0] * n
    highs[0] = highs[13] = highs[24] = 100.0
    lows[12] = 75.0                                # floor 75 → 25% box height
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = _make_ohlcv(closes, highs, lows)
    assert not detect_darvas_box(df).detected


def test_darvas_ceiling_not_retested_not_detected() -> None:
    """Ceiling touched only in the first fifth, then price drifts steadily
    lower — the resistance is stale and the tail forms no box of its own, so
    the retest gate rejects it."""
    n = 30
    highs = [100.0, 98.0, 98.0, 100.0, 98.0, 98.0, 100.0]   # 3 touches, all early
    highs += list(np.linspace(97.0, 80.0, n - len(highs)))   # strictly descending tail
    lows = [h - 3.0 for h in highs]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = _make_ohlcv(closes, highs, lows)
    assert not detect_darvas_box(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Inside bar breakout                                                  │
# ╰──────────────────────────────────────────────────────────────────────╯


def test_inside_bar_basic_detected() -> None:
    # Mother bar (index -2) high 110 / low 90; today fully inside.
    highs = [105, 108, 112, 110, 105]
    lows = [95, 92, 88, 90, 95]
    df = _make_ohlcv([(h + l) / 2 for h, l in zip(highs, lows)], highs, lows)
    result = detect_inside_bar_breakout(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(110.0)   # mother-bar high
    assert result.base_height == pytest.approx(20.0)


def test_inside_bar_also_nr7_gets_bonus() -> None:
    # 7 bars with wide ranges, then a tight inside bar inside a wide mother bar.
    highs = [120, 118, 122, 119, 121, 117, 130, 108]
    lows = [80, 82, 78, 81, 79, 83, 70, 106]
    df = _make_ohlcv([(h + l) / 2 for h, l in zip(highs, lows)], highs, lows)
    result = detect_inside_bar_breakout(df)
    assert result.detected
    assert result.notes.get("also_nr7") is True
    assert result.confidence >= 75.0


def test_inside_bar_tight_ratio_gets_bonus() -> None:
    # Inside range is a small fraction of the mother range (< 50%).
    highs = [105, 108, 112, 130, 102]     # mother high 130, low 70 (range 60); inside range 4
    lows = [95, 92, 88, 70, 98]
    df = _make_ohlcv([(h + l) / 2 for h, l in zip(highs, lows)], highs, lows)
    result = detect_inside_bar_breakout(df)
    assert result.detected
    assert result.notes["inside_ratio"] < 0.5


def test_inside_bar_higher_high_not_detected() -> None:
    highs = [105, 108, 100, 112]   # today's high 112 > mother 100
    lows = [95, 92, 90, 88]
    df = _make_ohlcv([(h + l) / 2 for h, l in zip(highs, lows)], highs, lows)
    assert not detect_inside_bar_breakout(df).detected


def test_inside_bar_lower_low_not_detected() -> None:
    highs = [105, 108, 110, 105]
    lows = [95, 92, 90, 85]        # today's low 85 < mother 90
    df = _make_ohlcv([(h + l) / 2 for h, l in zip(highs, lows)], highs, lows)
    assert not detect_inside_bar_breakout(df).detected


def test_inside_bar_single_bar_not_detected() -> None:
    df = _make_ohlcv([100.0], [101.0], [99.0])
    assert not detect_inside_bar_breakout(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Bollinger squeeze                                                    │
# ╰──────────────────────────────────────────────────────────────────────╯


def _squeeze_fixture(tighten: bool, upper_half: bool) -> pd.DataFrame:
    """130 bars: wide oscillation, then a linear tight ramp so the last bar's
    BB width is the 6-month minimum. `upper_half` places the last close above
    the 20-bar mean; `tighten=False` keeps volatility high at the end."""
    wide = [100 + 8 * ((-1) ** i) for i in range(110)]      # ±8 oscillation
    if tighten:
        tail = (
            list(np.linspace(104.0, 104.5, 20))
            if upper_half
            else list(np.linspace(104.5, 104.0, 20))  # descending → last close below mean
        )
    else:
        tail = [104 + 6 * ((-1) ** i) for i in range(20)]   # still wide at the end
    closes = wide + tail
    return _make_ohlcv(closes)


def test_bollinger_squeeze_detected() -> None:
    df = _squeeze_fixture(tighten=True, upper_half=True)
    result = detect_bollinger_squeeze(df)
    assert result.detected
    assert result.breakout_level > 0
    assert result.notes["squeeze_ratio"] < 1.0


def test_bollinger_squeeze_breakout_level_is_upper_band() -> None:
    df = _squeeze_fixture(tighten=True, upper_half=True)
    result = detect_bollinger_squeeze(df)
    assert result.detected
    # Upper band sits above the last close in a squeeze.
    assert result.breakout_level >= float(df["close"].iloc[-1])


def test_bollinger_squeeze_confidence_scales_with_tightness() -> None:
    df = _squeeze_fixture(tighten=True, upper_half=True)
    result = detect_bollinger_squeeze(df)
    assert result.detected
    assert result.confidence >= 70.0


def test_bollinger_squeeze_expanding_not_detected() -> None:
    df = _squeeze_fixture(tighten=False, upper_half=True)
    assert not detect_bollinger_squeeze(df).detected


def test_bollinger_squeeze_lower_half_not_detected() -> None:
    df = _squeeze_fixture(tighten=True, upper_half=False)
    assert not detect_bollinger_squeeze(df).detected


def test_bollinger_squeeze_insufficient_history_not_detected() -> None:
    df = _make_ohlcv([100.0] * 30)
    assert not detect_bollinger_squeeze(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Ascending triangle                                                   │
# ╰──────────────────────────────────────────────────────────────────────╯


def _triangle_fixture(
    peaks: list[float],
    troughs: list[float],
    n: int = 60,
) -> pd.DataFrame:
    """Zig-zag with isolated peaks/troughs so find_pivots(n=3) locks onto them.

    Highs baseline 97 with single-bar peaks at indices 5,15,25,...; lows
    baseline 95 with single-bar troughs at 10,20,30,.... Peaks/troughs are
    strict local extrema by construction.
    """
    highs = [97.0] * n
    lows = [95.0] * n
    for i, p in enumerate(peaks):
        highs[5 + i * 10] = p
    for i, t in enumerate(troughs):
        lows[10 + i * 10] = t
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return _make_ohlcv(closes, highs, lows)


def test_ascending_triangle_detected() -> None:
    df = _triangle_fixture(
        peaks=[100.0, 99.5, 100.0, 99.8, 100.0, 99.6],   # flat top within 2%
        troughs=[86.0, 88.0, 90.0, 92.0, 94.0],          # rising support
    )
    result = detect_ascending_triangle(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(100.0)
    assert result.notes["support_slope"] > 0
    assert result.notes["support_r2"] > 0.7


def test_ascending_triangle_touch_count_in_notes() -> None:
    df = _triangle_fixture(
        peaks=[100.0, 99.5, 100.0, 99.8, 100.0, 99.6],
        troughs=[86.0, 88.0, 90.0, 92.0, 94.0],
    )
    result = detect_ascending_triangle(df)
    assert result.detected
    assert result.notes["n_high_pivots"] >= 4
    assert result.notes["n_low_pivots"] >= 3


def test_ascending_triangle_early_lower_highs_still_detected() -> None:
    """Early highs sit >2% below resistance (made while price rose into the
    pattern). The naive 'all highs within 2%' reading rejected these; the
    cluster reading correctly detects the flat top formed by the later highs."""
    df = _triangle_fixture(
        peaks=[97.5, 97.8, 100.0, 99.5, 100.0, 99.8],    # first two below the 2% band
        troughs=[86.0, 88.0, 90.0, 92.0, 94.0],
    )
    result = detect_ascending_triangle(df)
    assert result.detected
    assert result.notes["n_resistance_touches"] >= 3
    assert result.breakout_level == pytest.approx(100.0)


def test_ascending_triangle_rising_lows_high_r2() -> None:
    df = _triangle_fixture(
        peaks=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        troughs=[85.0, 87.2, 89.4, 91.6, 93.8],          # perfectly linear
    )
    result = detect_ascending_triangle(df)
    assert result.detected
    assert result.notes["support_r2"] == pytest.approx(1.0, abs=1e-6)


def test_ascending_triangle_flat_lows_not_detected() -> None:
    df = _triangle_fixture(
        peaks=[100.0, 99.5, 100.0, 99.8, 100.0, 99.6],
        troughs=[90.0, 90.0, 90.0, 90.0, 90.0],          # support not rising
    )
    assert not detect_ascending_triangle(df).detected


def test_ascending_triangle_uneven_resistance_not_detected() -> None:
    df = _triangle_fixture(
        peaks=[100.0, 105.0, 95.0, 103.0, 97.0, 101.0],  # top spread > 2%
        troughs=[86.0, 88.0, 90.0, 92.0, 94.0],
    )
    assert not detect_ascending_triangle(df).detected


def test_ascending_triangle_trend_too_few_pivots_not_detected() -> None:
    closes = list(np.linspace(80, 120, 60))              # monotonic — no pivots
    df = _make_ohlcv(closes)
    assert not detect_ascending_triangle(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ VCP (volatility contraction pattern)                                 │
# ╰──────────────────────────────────────────────────────────────────────╯


def test_vcp_three_contractions_detected() -> None:
    # Pullbacks 25% → 14% → 8% (each < 60% of prior), volume tapering down.
    closes = _zigzag_path([60, 100, 75, 100, 88, 100, 94, 98])
    n = len(closes)
    volumes = list(np.linspace(200000, 50000, n))        # decreasing volume
    df = _make_ohlcv(closes, volumes=volumes)
    result = detect_vcp(df)
    assert result.detected
    assert result.notes["n_contractions"] >= 3
    assert result.notes["volume_decreasing"] is True


def test_vcp_contraction_depths_are_shrinking() -> None:
    closes = _zigzag_path([60, 100, 75, 100, 88, 100, 94, 98])
    df = _make_ohlcv(closes)
    result = detect_vcp(df)
    assert result.detected
    depths = result.notes["contractions"]
    assert all(depths[i] <= depths[i - 1] for i in range(1, len(depths)))


def test_vcp_breakout_level_near_recent_high() -> None:
    closes = _zigzag_path([60, 100, 75, 100, 88, 100, 94, 98])
    df = _make_ohlcv(closes)
    result = detect_vcp(df)
    assert result.detected
    assert result.breakout_level >= 92.0            # top of the final rally


def test_vcp_expanding_contractions_not_detected() -> None:
    # Depths grow (8% → 14% → 25%) — the opposite of a VCP.
    closes = _zigzag_path([60, 100, 92, 100, 86, 100, 75, 98])
    df = _make_ohlcv(closes)
    assert not detect_vcp(df).detected


def test_vcp_only_two_contractions_not_detected() -> None:
    closes = _zigzag_path([60, 100, 80, 100, 90, 98])   # only 2 pullbacks
    df = _make_ohlcv(closes)
    assert not detect_vcp(df).detected


def test_vcp_shallow_contraction_ratio_not_met_not_detected() -> None:
    # Depths 25% → 20% → 16%: each is 80% of the prior, not <= 60%.
    closes = _zigzag_path([60, 100, 75, 100, 80, 100, 84, 98])
    df = _make_ohlcv(closes)
    assert not detect_vcp(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Cup & handle                                                         │
# ╰──────────────────────────────────────────────────────────────────────╯


def _cup_fixture(
    left_lip: float,
    bottom: float,
    right_lip: float,
    handle_from: float,
    handle_to: float,
    cup_half_bars: int = 20,
    handle_down_bars: int = 5,
    handle_up_bars: int = 5,
) -> pd.DataFrame:
    down = list(np.linspace(left_lip, bottom, cup_half_bars))
    up = list(np.linspace(bottom, right_lip, cup_half_bars))[1:]
    h_down = list(np.linspace(right_lip, handle_from, 2))[1:] + list(
        np.linspace(handle_from, handle_to, handle_down_bars)
    )
    h_up = list(np.linspace(handle_to, (handle_to + right_lip) / 2, handle_up_bars))
    closes = down + up + h_down + h_up
    return _make_ohlcv(closes)


def test_cup_and_handle_detected() -> None:
    df = _cup_fixture(left_lip=100.0, bottom=80.0, right_lip=99.0,
                      handle_from=98.0, handle_to=92.0)
    result = detect_cup_and_handle(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(99.0, abs=0.5)
    assert 0.12 <= result.notes["cup_depth"] <= 0.35


def test_cup_and_handle_handle_shallower_than_cup() -> None:
    df = _cup_fixture(left_lip=100.0, bottom=80.0, right_lip=99.0,
                      handle_from=98.0, handle_to=92.0)
    result = detect_cup_and_handle(df)
    assert result.detected
    assert result.notes["handle_depth"] < result.notes["cup_depth"]


def test_cup_and_handle_lips_are_close() -> None:
    df = _cup_fixture(left_lip=100.0, bottom=82.0, right_lip=98.0,
                      handle_from=97.0, handle_to=93.0)
    result = detect_cup_and_handle(df)
    assert result.detected
    assert abs(result.notes["right_lip"] - result.notes["left_lip"]) / result.notes["left_lip"] <= 0.05


def test_cup_and_handle_too_shallow_cup_not_detected() -> None:
    # Cup only ~5% deep — below the 12% floor.
    df = _cup_fixture(left_lip=100.0, bottom=95.0, right_lip=99.0,
                      handle_from=98.5, handle_to=97.0)
    assert not detect_cup_and_handle(df).detected


def test_cup_and_handle_deep_handle_not_detected() -> None:
    # Handle plunges below the cup midpoint (deeper than allowed).
    df = _cup_fixture(left_lip=100.0, bottom=80.0, right_lip=99.0,
                      handle_from=98.0, handle_to=84.0)
    assert not detect_cup_and_handle(df).detected


def test_cup_and_handle_pure_uptrend_not_detected() -> None:
    closes = list(np.linspace(80, 120, 60))
    df = _make_ohlcv(closes)
    assert not detect_cup_and_handle(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Flag                                                                 │
# ╰──────────────────────────────────────────────────────────────────────╯


def _flag_fixture(
    base: float = 80.0,
    base_bars: int = 20,
    pole_to: float = 96.0,
    pole_bars: int = 10,
    flag_to: float = 90.0,
    flag_bars: int = 8,
    pole_high_volume: bool = True,
) -> pd.DataFrame:
    base_seg = [base] * base_bars
    pole = list(np.linspace(base, pole_to, pole_bars))
    flag = list(np.linspace(pole_to, flag_to, flag_bars))
    closes = base_seg + pole + flag
    if pole_high_volume:
        volumes = [50000.0] * base_bars + [200000.0] * pole_bars + [90000.0] * flag_bars
    else:
        # Uniform volume everywhere → the pole can never trade above the
        # running average, so the volume gate must reject it.
        volumes = [60000.0] * (base_bars + pole_bars + flag_bars)
    return _make_ohlcv(closes, volumes=volumes)


def test_flag_detected() -> None:
    df = _flag_fixture()
    result = detect_flag(df)
    assert result.detected
    assert result.notes["pole_move"] >= 0.15


def test_flag_measured_move_uses_pole_height() -> None:
    df = _flag_fixture()
    result = detect_flag(df)
    assert result.detected
    assert result.base_height > 0


def test_flag_breakout_level_is_flag_top() -> None:
    df = _flag_fixture()
    result = detect_flag(df)
    assert result.detected
    assert result.breakout_level >= float(df["close"].iloc[-1])


def test_flag_weak_pole_not_detected() -> None:
    # Pole is only ~6% — below the 15% requirement.
    df = _flag_fixture(base=80.0, pole_to=85.0, flag_to=83.0)
    assert not detect_flag(df).detected


def test_flag_rising_flag_not_detected() -> None:
    # "Flag" keeps making new highs (no counter-trend consolidation).
    df = _flag_fixture(pole_to=96.0, flag_to=104.0)
    assert not detect_flag(df).detected


def test_flag_low_volume_pole_not_detected() -> None:
    df = _flag_fixture(pole_high_volume=False)
    assert not detect_flag(df).detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ NR7                                                                  │
# ╰──────────────────────────────────────────────────────────────────────╯


def test_nr7_today_smallest_range_detected() -> None:
    """7 bars where the last has the smallest TR."""
    # Build 7 bars with TRs 5, 4, 4, 3, 3, 2, 1
    highs = [105, 104, 104, 103, 103, 102, 100.5]
    lows = [100, 100, 100, 100, 100, 100, 99.5]
    closes = [102, 102, 102, 101.5, 101.5, 101, 100]
    df = _make_ohlcv(closes, highs, lows)
    result = detect_nr7(df)
    assert result.detected
    assert result.breakout_level == pytest.approx(100.5)


def test_nr7_with_volume_contraction_has_higher_confidence() -> None:
    """Low volume on the NR7 bar should boost confidence."""
    # 20+ bars (so vol_contraction check has data), last is NR7
    rng_highs = [110 + i * 0.1 for i in range(25)]
    rng_lows = [108 + i * 0.1 for i in range(25)]
    rng_closes = [(h + l) / 2 for h, l in zip(rng_highs, rng_lows)]
    # Make the last bar a tight NR7
    rng_highs[-1] = 113.3
    rng_lows[-1] = 113.1  # TR ≈ 0.2, smallest of last 7
    rng_closes[-1] = 113.2
    # First fixture: high volume on NR7
    high_vol = [100000] * 24 + [200000]
    # Second fixture: low volume on NR7
    low_vol = [100000] * 24 + [50000]
    df_high = _make_ohlcv(rng_closes, rng_highs, rng_lows, high_vol)
    df_low = _make_ohlcv(rng_closes, rng_highs, rng_lows, low_vol)
    high_res = detect_nr7(df_high)
    low_res = detect_nr7(df_low)
    assert high_res.detected
    assert low_res.detected
    assert low_res.confidence > high_res.confidence


def test_nr7_at_swing_high_has_higher_confidence() -> None:
    """NR7 printing near a recent swing high should pick up the proximity bonus.

    Two fixtures are constructed:
      - `df_plain`: smooth uptrend; no swing high near today's price.
      - `df_pivot`: same shape, but with a clear swing high at the level
        where today's NR7 happens to print.

    The detector should fire on both; the second should show higher confidence
    via the `near_pivot_high` bonus.
    """
    # Plain: 30 bars rising smoothly, last bar is NR7.
    closes_plain = list(np.linspace(100, 110, 29)) + [109.85]
    highs_plain = [c + 1 for c in closes_plain]
    lows_plain = [c - 1 for c in closes_plain]
    # Tighten today's bar so it's the NR7
    highs_plain[-1] = 109.95
    lows_plain[-1] = 109.75
    df_plain = _make_ohlcv(closes_plain, highs_plain, lows_plain)

    # Pivot: ramps to 105, jumps to a 110.5 swing high at idx 22, then
    # consolidates back to ~108-109 (low enough that 110.5 stays the local
    # max), ending with an NR7 whose high comes within 2% of the swing high.
    closes_pivot = (
        list(np.linspace(100, 105, 22))
        + [110.0]
        + list(np.linspace(108.0, 109.5, 6))
        + [109.9]
    )
    assert len(closes_pivot) == 30
    highs_pivot = [c + 1 for c in closes_pivot]
    lows_pivot = [c - 1 for c in closes_pivot]
    highs_pivot[22] = 110.5     # the swing high (above close+1)
    # NR7 at the end: tight bar near the swing high price
    highs_pivot[-1] = 110.1
    lows_pivot[-1] = 109.9
    df_pivot = _make_ohlcv(closes_pivot, highs_pivot, lows_pivot)

    r_plain = detect_nr7(df_plain)
    r_pivot = detect_nr7(df_pivot)
    assert r_plain.detected
    assert r_pivot.detected
    assert r_pivot.notes["near_pivot_high"] is True
    assert r_plain.notes["near_pivot_high"] is False
    assert r_pivot.confidence > r_plain.confidence


def test_nr7_not_smallest_range_not_detected() -> None:
    """If today's TR is not the smallest of last 7, no detection."""
    highs = [105, 104, 104, 103, 103, 102, 105]  # last bar has range 5, not smallest
    lows = [100, 100, 100, 100, 100, 100, 100]
    closes = [102, 102, 102, 101.5, 101.5, 101, 102.5]
    df = _make_ohlcv(closes, highs, lows)
    result = detect_nr7(df)
    assert not result.detected


def test_nr7_insufficient_data_not_detected() -> None:
    df = _make_ohlcv([100, 101, 102])  # only 3 bars
    result = detect_nr7(df)
    assert not result.detected


def test_nr7_tied_minimum_still_detected() -> None:
    """If multiple bars tie at the minimum TR including today, still NR7."""
    highs = [105, 104, 104, 103, 102, 101.5, 101.5]
    lows = [100, 100, 100, 100, 100, 100, 100]
    closes = [102, 102, 102, 101.5, 101, 100.75, 101.25]
    df = _make_ohlcv(closes, highs, lows)
    result = detect_nr7(df)
    assert result.detected


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Dispatcher                                                            │
# ╰──────────────────────────────────────────────────────────────────────╯


def test_detect_all_only_returns_detected_matches() -> None:
    df = _make_ohlcv(list(np.linspace(100, 110, 60)))  # uptrend, no patterns expected
    matches = detect_all(df, list(PATTERN_REGISTRY.keys()))
    for m in matches:
        assert m.detected


def test_detect_all_sorted_by_confidence_descending() -> None:
    """When multiple patterns fire we should get the strongest first."""
    # Force a 52w high setup AND NR7 at the end
    closes = list(np.linspace(100, 200, 252))
    closes[-1] = closes[-2] * 0.999  # near 52w high
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    # Make last 7 bars compress toward NR7
    for i in range(-7, 0):
        rng = abs(i) * 0.5
        highs[i] = closes[i] + rng
        lows[i] = closes[i] - rng
    df = _make_ohlcv(closes, highs, lows)
    matches = detect_all(df, list(PATTERN_REGISTRY.keys()))
    confidences = [m.confidence for m in matches]
    assert confidences == sorted(confidences, reverse=True)


def test_detect_all_with_unknown_pattern_name_skipped() -> None:
    df = _make_ohlcv(list(np.linspace(100, 110, 60)))
    matches = detect_all(df, ["fifty_two_week_high", "totally_made_up"])
    # Should not raise and should silently skip the unknown name
    assert isinstance(matches, list)


def test_pattern_match_default_no_match_is_falsey() -> None:
    """A no-match PatternMatch's `detected` is False so callers can `if match.detected:`."""
    m = PatternMatch(detected=False, pattern_name="x")
    assert not m.detected
    assert m.confidence == 0.0
    assert m.breakout_level == 0.0
