"""Chart pattern detectors.

Each detector is a pure function: takes an OHLCV DataFrame and returns a
single `PatternMatch`. A non-detection returns a PatternMatch with
`detected=False` and zero-valued fields — callers should check `detected`
before consuming any other field.

Nine detectors are fully implemented and enabled by default (see config.yaml):
  1. fifty_two_week_high        — 52-week high breakout with resistance touches
  2. darvas_box                 — Box breakout (parallel highs and lows)
  3. nr7                        — Narrow range (NR7) compression breakout
  4. inside_bar                 — Inside bar (range contraction) breakout
  5. bollinger_squeeze          — Bollinger band squeeze (volatility contraction)
  6. ascending_triangle         — Ascending triangle (bullish accumulation)
  7. vcp                        — Volatility Contraction Pattern (Minervini)
  8. cup_and_handle             — Cup & Handle formation
  9. flag                       — Flag pattern (short-term consolidation)

Each detector is configured independently in config.yaml's `patterns.enabled` list.
Each returns a `PatternMatch` with confidence (0–100), breakout level, base height
for measured-move targets, and metadata notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import linregress

from breakout.analysis.indicators import (
    bollinger_band_width,
    bollinger_bands,
    true_range,
)
from breakout.analysis.pivots import find_pivots


# ── Result type ──────────────────────────────────────────────────────────


@dataclass
class PatternMatch:
    """A single pattern detection result.

    When detected=False, downstream code should not rely on the other fields;
    they're set to sensible defaults but represent "no signal", not "zero".
    """

    detected: bool
    pattern_name: str
    confidence: float = 0.0          # 0-100
    breakout_level: float = 0.0      # price the stock needs to close above
    base_start_idx: int = -1
    base_end_idx: int = -1
    base_height: float = 0.0          # for measured-move target
    notes: dict[str, Any] = field(default_factory=dict)


def _no_match(name: str) -> PatternMatch:
    return PatternMatch(detected=False, pattern_name=name)


# ── 52-week high breakout ────────────────────────────────────────────────


# Lookback windows. 252 trading days ≈ 1 calendar year on Indian NSE.
_FIFTY_TWO_WEEK_BARS = 252
# Tolerance below the 52-week high for "close to" qualification.
# ⚠️  TUNING REQUIRED: These thresholds are borrowed from Stan Weinstein's US
# playbook and have NOT been backtested on Indian NSE data. Consider tuning:
#  - _NEAR_52W_TOLERANCE_PCT: try 0.01, 0.02, 0.03 (wider = more signals)
#  - _TOUCH_TOLERANCE_PCT: try 0.01, 0.015, 0.02 (wider = more touches)
# Backtest against 2020-2024 data on NIFTY 500 and adjust based on win rate.
_NEAR_52W_TOLERANCE_PCT = 0.02
# Window used to count touches (proxies "how flat is the resistance").
_TOUCH_COUNT_LOOKBACK = 126  # 6 months
# A "touch" is a bar whose high comes within this fraction of the 52w high.
_TOUCH_TOLERANCE_PCT = 0.015


def _safe_pivot_lookback_bars(df: pd.DataFrame, n: int = 5) -> int:
    """Safe lookback ensuring we don't use unconfirmed pivots.

    Pivots require `n` bars on each side to confirm. The most recent `n` bars
    cannot be confirmed yet. This helper ensures pattern detectors only use
    pivots that are at least `n` bars away from today.

    Args:
        df: OHLCV DataFrame
        n: Confirmation window for pivots (default 5)

    Returns:
        Safe lookback index (0-based): patterns should only use data up to this
    """
    return max(0, len(df) - 1 - n)


def detect_fifty_two_week_high_breakout(df: pd.DataFrame) -> PatternMatch:
    """Detects a stock setting up to break its 52-week high.

    Condition: close is within `_NEAR_52W_TOLERANCE_PCT` (2%) of the 252-day
    high. Breakout level is the 52-week high itself. Confidence scales with
    how many times in the last 6 months the stock approached this level
    without breaking — a flatter resistance is a higher-conviction setup.

    Returns:
        PatternMatch with breakout_level = 52w high, base spanning the touch
        window, base_height for measured-move target = 52w_high - base_low.
    """
    name = "fifty_two_week_high"
    if len(df) < _FIFTY_TWO_WEEK_BARS:
        return _no_match(name)

    recent = df.iloc[-_FIFTY_TWO_WEEK_BARS:]
    fifty_two_week_high = float(recent["high"].max())
    close = float(df["close"].iloc[-1])

    # max_high >= today's high >= today's close, so distance_pct is always >= 0.
    # The 2% window catches "about to break out" setups; once close is more than
    # 2% below the 52w high we treat it as too far away to count as a setup.
    distance_pct = (fifty_two_week_high - close) / fifty_two_week_high
    if distance_pct > _NEAR_52W_TOLERANCE_PCT:
        return _no_match(name)

    # Count touches in the last 6 months
    touch_window = df.iloc[-_TOUCH_COUNT_LOOKBACK:] if len(df) >= _TOUCH_COUNT_LOOKBACK else df
    touch_threshold = fifty_two_week_high * (1 - _TOUCH_TOLERANCE_PCT)
    touches = int((touch_window["high"] >= touch_threshold).sum())

    # Base spans from the earliest qualifying touch to today
    high_arr = touch_window["high"].to_numpy()
    near_mask = high_arr >= touch_threshold
    if near_mask.any():
        first_touch_offset = int(np.argmax(near_mask))
    else:
        first_touch_offset = 0
    base_start_idx = len(df) - len(touch_window) + first_touch_offset
    base_end_idx = len(df) - 1
    base_low = float(df["low"].iloc[base_start_idx : base_end_idx + 1].min())
    base_height = fifty_two_week_high - base_low

    # Confidence: 50 baseline, +5 per touch above the first, capped at 100.
    # Each additional approach to the resistance is a small marginal signal —
    # going too steep saturates the scale on routine multi-test setups.
    confidence = min(100.0, 50.0 + max(0, touches - 1) * 5.0)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=fifty_two_week_high,
        base_start_idx=base_start_idx,
        base_end_idx=base_end_idx,
        base_height=base_height,
        notes={
            "touches_in_6mo": touches,
            "distance_pct_from_high": distance_pct,
            "base_low": base_low,
        },
    )


# ── Darvas Box ───────────────────────────────────────────────────────────


# Box geometry. A Darvas box is a consolidation range that *opens* when a high
# establishes a ceiling, then trades below it until it either breaks out or
# breaks down. The defining properties, and how we test each:
#
#   - The box opens on a high that sets the ceiling. We require the window's
#     first bar to be a ceiling touch (its high within TOUCH_TOLERANCE of the
#     window's max high). This is what pins the box's left edge and — crucially
#     — stops a preceding run-up from being swallowed into the "box".
#   - It's a consolidation, not a trend: bounded height (ceiling-floor)/ceiling.
#   - The ceiling is a *tested* resistance: at least MIN_TOUCHES highs reach it,
#     and it's retested in the box's second half (last touch past the midpoint).
#
# NOTE: the previous implementation required *every* daily high to sit within
# 3% of the ceiling. That describes a flat-lined stock, not a box (price pulls
# back inside a box, so pullback highs sit well below the ceiling), and matched
# only 2 of the NIFTY 500. See tests for the corrected shape.
_DARVAS_MAX_HEIGHT = 0.15           # (ceiling - floor) / ceiling — consolidation cap
_DARVAS_MIN_DAYS = 20
_DARVAS_MAX_DAYS = 80
_DARVAS_MIN_TOUCHES = 3
_DARVAS_TOUCH_TOLERANCE = 0.01      # high within 1% of the ceiling counts as a touch
_DARVAS_RETEST_FRACTION = 0.5       # last ceiling touch must fall past this fraction


def detect_darvas_box(df: pd.DataFrame) -> PatternMatch:
    """Detect a Darvas Box ending on the last bar.

    Scans candidate window lengths from longest to shortest and returns the
    longest window that:
      1. opens on a ceiling touch (first bar's high within 1% of the box max),
      2. is a bounded consolidation (height <= 15% of the ceiling),
      3. touches the ceiling at least 3 times, with the last touch in the
         box's second half (a live, retested resistance).

    Breakout level = the ceiling. Measured-move target uses base_height =
    ceiling - floor.
    """
    name = "darvas_box"
    if len(df) < _DARVAS_MIN_DAYS:
        return _no_match(name)

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    last = len(df) - 1

    # Search longest-to-shortest so we return the most-established box.
    max_window = min(_DARVAS_MAX_DAYS, len(df))
    for window_len in range(max_window, _DARVAS_MIN_DAYS - 1, -1):
        start = last - window_len + 1
        if start < 0:
            continue
        window_high = high[start : last + 1]
        window_low = low[start : last + 1]

        ceiling = float(window_high.max())
        floor = float(window_low.min())
        if floor <= 0 or ceiling <= 0:
            continue

        touch_threshold = ceiling * (1 - _DARVAS_TOUCH_TOLERANCE)

        # 1. The box must OPEN on a ceiling touch — this sets the left edge and
        #    prevents a preceding run-up from being absorbed into the box.
        if window_high[0] < touch_threshold:
            continue

        # 2. Bounded consolidation.
        rel_width = (ceiling - floor) / ceiling
        if rel_width > _DARVAS_MAX_HEIGHT:
            continue

        # 3. Ceiling tested >= MIN_TOUCHES times, and retested in the 2nd half.
        touch_positions = np.nonzero(window_high >= touch_threshold)[0]
        touches = int(touch_positions.size)
        if touches < _DARVAS_MIN_TOUCHES:
            continue
        if int(touch_positions[-1]) < _DARVAS_RETEST_FRACTION * (window_len - 1):
            continue

        base_height = ceiling - floor

        # Confidence: 60 baseline + 5 per extra touch (capped +20) + tightness bonus.
        extra_touches = touches - _DARVAS_MIN_TOUCHES
        touch_bonus = min(20.0, extra_touches * 5.0)
        # Tightness: a narrower box relative to its ceiling is higher-conviction.
        tightness_bonus = 10.0 if rel_width < 0.08 else (5.0 if rel_width < 0.12 else 0.0)
        confidence = min(100.0, 60.0 + touch_bonus + tightness_bonus)

        return PatternMatch(
            detected=True,
            pattern_name=name,
            confidence=confidence,
            breakout_level=ceiling,
            base_start_idx=start,
            base_end_idx=last,
            base_height=base_height,
            notes={
                "ceiling": ceiling,
                "floor": floor,
                "touches": touches,
                "window_len": window_len,
                "relative_width": rel_width,
            },
        )

    return _no_match(name)


# ── NR7 ──────────────────────────────────────────────────────────────────


_NR7_LOOKBACK = 7
# How close to a recent swing high counts as "at or near" for the bonus.
_NR7_NEAR_PIVOT_PCT = 0.02
_NR7_SWING_LOOKBACK = 30


def detect_nr7(df: pd.DataFrame) -> PatternMatch:
    """Today's true range is the smallest of the last 7 days.

    Often precedes a directional move because contracted range = compressed
    energy. Highest confidence when NR7 prints at or near a recent swing high
    (an "NR7 at resistance" setup, which is the classic Crabel-style trigger).
    """
    name = "nr7"
    if len(df) < _NR7_LOOKBACK:
        return _no_match(name)

    tr = true_range(df)
    last_n_tr = tr.iloc[-_NR7_LOOKBACK:]
    if last_n_tr.isna().any():
        return _no_match(name)
    if tr.iloc[-1] != last_n_tr.min():
        return _no_match(name)
    # Defensive: in case multiple bars tie at the min, still count it (today is one of the minimums).

    breakout_level = float(df["high"].iloc[-1])
    today_low = float(df["low"].iloc[-1])

    # Bonus: near a recent swing high?
    near_pivot = False
    recent = df.iloc[-_NR7_SWING_LOOKBACK:] if len(df) >= _NR7_SWING_LOOKBACK else df
    pivots = find_pivots(recent, n=3)
    if pivots.highs:
        for piv in pivots.highs[-3:]:
            if abs(piv.price - breakout_level) / piv.price <= _NR7_NEAR_PIVOT_PCT:
                near_pivot = True
                break

    # Bonus: volume contraction (today's volume below recent average)
    vol_contraction = False
    if "volume" in df.columns and len(df) >= 20:
        recent_avg_vol = df["volume"].iloc[-20:-1].mean()
        if df["volume"].iloc[-1] < recent_avg_vol:
            vol_contraction = True

    confidence = 50.0
    if near_pivot:
        confidence += 30.0
    if vol_contraction:
        confidence += 20.0
    confidence = min(100.0, confidence)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=breakout_level,
        base_start_idx=len(df) - _NR7_LOOKBACK,
        base_end_idx=len(df) - 1,
        base_height=breakout_level - today_low,
        notes={
            "today_tr": float(tr.iloc[-1]),
            "min_tr_last_7": float(last_n_tr.min()),
            "near_pivot_high": near_pivot,
            "volume_contraction": vol_contraction,
        },
    )


# ── Inside bar breakout ────────────────────────────────────────────────────


_INSIDE_BAR_TIGHT_RATIO = 0.5   # inside range < 50% of mother range → tightness bonus


def detect_inside_bar_breakout(df: pd.DataFrame) -> PatternMatch:
    """Today's bar is fully inside yesterday's ("mother") bar.

    Condition: today's high < yesterday's high AND today's low > yesterday's
    low. The breakout level is the mother bar's high. Confidence gets a bonus
    when today is also an NR7 (compressed range), and when the inside bar is
    tight relative to the mother bar.
    """
    name = "inside_bar"
    if len(df) < 2:
        return _no_match(name)

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    today_high, today_low = float(high[-1]), float(low[-1])
    mother_high, mother_low = float(high[-2]), float(low[-2])

    if not (today_high < mother_high and today_low > mother_low):
        return _no_match(name)

    mother_range = mother_high - mother_low
    inside_range = today_high - today_low
    ratio = inside_range / mother_range if mother_range > 0 else 1.0

    confidence = 55.0
    notes: dict[str, Any] = {"inside_ratio": ratio}

    # NR7 bonus: today's true range is the smallest of the last 7 bars.
    if len(df) >= _NR7_LOOKBACK:
        tr = true_range(df)
        last7 = tr.iloc[-_NR7_LOOKBACK:]
        if not last7.isna().any() and tr.iloc[-1] == last7.min():
            confidence += 20.0
            notes["also_nr7"] = True
    if ratio < _INSIDE_BAR_TIGHT_RATIO:
        confidence += 10.0
    confidence = min(100.0, confidence)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=mother_high,
        base_start_idx=len(df) - 2,
        base_end_idx=len(df) - 1,
        base_height=mother_range,
        notes=notes,
    )


# ── Bollinger squeeze ──────────────────────────────────────────────────────


_BB_PERIOD = 20
_BB_STD = 2.0
_BB_SQUEEZE_LOOKBACK = 126    # 6 months of trading days
_BB_MIN_EPS = 1.001           # current width must be at (within 0.1% of) the min
_BB_MIN_HISTORY = 40          # need a meaningful window to call a "6-month min"


def detect_bollinger_squeeze(df: pd.DataFrame) -> PatternMatch:
    """Bollinger Band width at a 6-month minimum, price in the upper half.

    A squeeze (contracted bands) precedes expansion; combined with price in the
    upper half of the bands it biases the expansion upward. Breakout level is
    the upper band.
    """
    name = "bollinger_squeeze"
    if len(df) < _BB_PERIOD + _BB_MIN_HISTORY:
        return _no_match(name)

    close = df["close"]
    width = bollinger_band_width(close, _BB_PERIOD, _BB_STD)
    if pd.isna(width.iloc[-1]):
        return _no_match(name)

    valid = width.dropna()
    lookback = min(_BB_SQUEEZE_LOOKBACK, len(valid))
    recent = valid.iloc[-lookback:]
    if len(recent) < _BB_MIN_HISTORY:
        return _no_match(name)

    current = float(width.iloc[-1])
    if current > float(recent.min()) * _BB_MIN_EPS:
        return _no_match(name)   # not at the squeeze minimum

    upper, middle, lower = bollinger_bands(close, _BB_PERIOD, _BB_STD)
    c = float(close.iloc[-1])
    mid = float(middle.iloc[-1])
    if c < mid:                  # must sit in the upper half of the bands
        return _no_match(name)

    breakout_level = float(upper.iloc[-1])
    base_height = float(upper.iloc[-1] - lower.iloc[-1])

    confidence = 60.0
    median_w = float(recent.median())
    squeeze_ratio = current / median_w if median_w > 0 else 1.0
    if squeeze_ratio < 0.6:
        confidence += 20.0
    elif squeeze_ratio < 0.8:
        confidence += 10.0
    confidence = min(100.0, confidence)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=breakout_level,
        base_start_idx=len(df) - lookback,
        base_end_idx=len(df) - 1,
        base_height=base_height,
        notes={
            "bb_width": current,
            "median_width_6mo": median_w,
            "squeeze_ratio": squeeze_ratio,
            "close_vs_mid": c / mid if mid else 0.0,
        },
    )


# ── Ascending triangle ─────────────────────────────────────────────────────


_TRI_MIN_DAYS = 20
_TRI_MAX_DAYS = 80
_TRI_PIVOT_N = 3
_TRI_FLAT_TOL = 0.02      # a "touch": swing high within 2% of the top of the cluster
_TRI_MIN_R2 = 0.7         # rising support regression fit
_TRI_MIN_HIGH_PIVOTS = 4
_TRI_MIN_LOW_PIVOTS = 3
_TRI_MIN_HIGH_TOUCHES = 3  # swing highs clustered near resistance


def detect_ascending_triangle(df: pd.DataFrame) -> PatternMatch:
    """Flat resistance (horizontal top) with a rising support line.

    Fits a horizontal line through 4+ swing highs (max spread 2%) and a
    rising regression line through 3+ swing lows (positive slope, r² > 0.7).
    Breakout level is the flat resistance.
    """
    name = "ascending_triangle"
    if len(df) < _TRI_MIN_DAYS:
        return _no_match(name)

    window = df.iloc[-min(_TRI_MAX_DAYS, len(df)):]
    pivots = find_pivots(window, n=_TRI_PIVOT_N)
    if len(pivots.highs) < _TRI_MIN_HIGH_PIVOTS or len(pivots.lows) < _TRI_MIN_LOW_PIVOTS:
        return _no_match(name)

    high_prices = np.array([p.price for p in pivots.highs], dtype=float)
    top = float(high_prices.max())
    if top <= 0:
        return _no_match(name)
    # Flat resistance = the cluster of swing highs near the top. The lower
    # swing highs made while price rose *into* the pattern are not part of the
    # resistance and must not count against its flatness — measuring every
    # high's spread (the naive reading) matched only 1/500 real stocks.
    touch_prices = high_prices[(top - high_prices) / top <= _TRI_FLAT_TOL]
    if len(touch_prices) < _TRI_MIN_HIGH_TOUCHES:
        return _no_match(name)   # not enough highs cluster at a flat resistance

    xs = np.array([p.index for p in pivots.lows], dtype=float)
    ys = np.array([p.price for p in pivots.lows], dtype=float)
    reg = linregress(xs, ys)
    r2 = float(reg.rvalue) ** 2
    if reg.slope <= 0 or r2 < _TRI_MIN_R2:
        return _no_match(name)   # support not convincingly rising

    resistance = top
    support_now = float(reg.intercept + reg.slope * (len(window) - 1))
    base_height = resistance - support_now if resistance > support_now else resistance - float(ys.min())

    confidence = 60.0
    confidence += min(20.0, (r2 - _TRI_MIN_R2) / (1.0 - _TRI_MIN_R2) * 20.0)
    confidence += min(10.0, (len(touch_prices) - _TRI_MIN_HIGH_TOUCHES) * 3.0)
    confidence = min(100.0, confidence)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=resistance,
        base_start_idx=len(df) - len(window),
        base_end_idx=len(df) - 1,
        base_height=float(base_height),
        notes={
            "resistance": resistance,
            "support_slope": float(reg.slope),
            "support_r2": r2,
            "n_resistance_touches": int(len(touch_prices)),
            "n_high_pivots": len(pivots.highs),
            "n_low_pivots": len(pivots.lows),
        },
    )


# ── VCP (Volatility Contraction Pattern) ───────────────────────────────────


_VCP_LOOKBACK = 250
_VCP_PIVOT_N = 3
_VCP_MIN_CONTRACTIONS = 3
_VCP_RATIO = 0.6          # each contraction <= 60% of the previous one


def _zigzag(pivots) -> list[tuple[int, str, float]]:
    """Merge high/low pivots into a strictly alternating H/L sequence.

    When two same-type pivots are adjacent (no opposite pivot between them),
    keep the more extreme one so the result alternates cleanly.
    """
    merged = sorted(
        [(p.index, "H", p.price) for p in pivots.highs]
        + [(p.index, "L", p.price) for p in pivots.lows]
    )
    zig: list[tuple[int, str, float]] = []
    for idx, kind, price in merged:
        if not zig:
            zig.append((idx, kind, price))
            continue
        if kind == zig[-1][1]:
            keep_new = (kind == "H" and price > zig[-1][2]) or (
                kind == "L" and price < zig[-1][2]
            )
            if keep_new:
                zig[-1] = (idx, kind, price)
        else:
            zig.append((idx, kind, price))
    return zig


def detect_vcp(df: pd.DataFrame) -> PatternMatch:
    """Volatility Contraction Pattern (Minervini).

    A sequence of 3+ pullbacks whose depths contract (each <= 60% of the
    previous), ideally on decreasing volume. Breakout level is the highest
    high of the most recent rally (the top of the final, tightest contraction).
    """
    name = "vcp"
    if len(df) < 2 * _VCP_PIVOT_N + 2:
        return _no_match(name)

    window = df.iloc[-min(_VCP_LOOKBACK, len(df)):].reset_index(drop=True)
    pivots = find_pivots(window, n=_VCP_PIVOT_N)
    zig = _zigzag(pivots)

    # Contractions: each high followed by the next low (a pullback).
    contractions = []  # (depth, high_idx, low_idx, high_price, low_price)
    for j in range(len(zig) - 1):
        if zig[j][1] == "H" and zig[j + 1][1] == "L":
            h, low_ = zig[j][2], zig[j + 1][2]
            if h > 0 and h > low_:
                contractions.append(((h - low_) / h, zig[j][0], zig[j + 1][0], h, low_))
    if len(contractions) < _VCP_MIN_CONTRACTIONS:
        return _no_match(name)

    # Longest trailing run where each contraction <= 60% of the previous.
    run = [contractions[-1]]
    for k in range(len(contractions) - 2, -1, -1):
        newer, older = contractions[k + 1][0], contractions[k][0]
        if newer <= older * _VCP_RATIO:
            run.insert(0, contractions[k])
        else:
            break
    if len(run) < _VCP_MIN_CONTRACTIONS:
        return _no_match(name)

    vols = [float(window["volume"].iloc[c[1] : c[2] + 1].mean()) for c in run]
    vol_decreasing = all(vols[i] <= vols[i - 1] for i in range(1, len(vols)))

    last_low_idx = run[-1][2]
    breakout_level = float(window["high"].iloc[last_low_idx:].max())
    base_height = breakout_level - run[-1][4]

    confidence = 60.0
    if vol_decreasing:
        confidence += 15.0
    confidence += min(15.0, (len(run) - _VCP_MIN_CONTRACTIONS) * 7.5)
    confidence = min(100.0, confidence)

    return PatternMatch(
        detected=True,
        pattern_name=name,
        confidence=confidence,
        breakout_level=breakout_level,
        base_start_idx=len(df) - len(window) + run[0][1],
        base_end_idx=len(df) - 1,
        base_height=float(base_height),
        notes={
            "contractions": [round(c[0], 3) for c in run],
            "volume_decreasing": vol_decreasing,
            "n_contractions": len(run),
        },
    )


# ── Cup & Handle ────────────────────────────────────────────────────────────


_CUP_LOOKBACK = 180
_CUP_MIN_TOTAL = 40
_CUP_MIN_BARS = 20        # cup (lip-to-lip) minimum bars ≈ 4 weeks
_CUP_MAX_DUR = 130        # ≈ 26 weeks
_CUP_MIN_DEPTH = 0.12
_CUP_MAX_DEPTH = 0.35
_CUP_LIP_TOL = 0.05       # right lip within 5% of left lip
_HANDLE_MIN = 5           # 1 week
_HANDLE_MAX = 25          # 5 weeks
_HANDLE_MIN_DEPTH = 0.04
_HANDLE_MAX_DEPTH = 0.20


def detect_cup_and_handle(df: pd.DataFrame) -> PatternMatch:
    """Cup-and-handle: a rounded U (left lip → bottom → right lip) followed by
    a shallow handle consolidation below the right lip.

    Breakout level is the right cup lip. The handle must be 4-20% deep, 1-5
    weeks long, and stay above the cup's midpoint.
    """
    name = "cup_and_handle"
    if len(df) < _CUP_MIN_TOTAL:
        return _no_match(name)

    window = df.iloc[-min(_CUP_LOOKBACK, len(df)):]
    high = window["high"].to_numpy()
    low = window["low"].to_numpy()
    n = len(window)

    # The handle is the most recent segment; scan handle lengths shortest-first.
    for hl in range(_HANDLE_MIN, _HANDLE_MAX + 1):
        cup_end = n - hl
        if cup_end < _CUP_MIN_BARS:
            break
        cup_high = high[:cup_end]
        cup_low = low[:cup_end]
        half = len(cup_high) // 2

        left_lip_idx = int(np.argmax(cup_high[:half]))
        left_lip = float(cup_high[left_lip_idx])
        right_lip_rel = int(np.argmax(cup_high[half:]))
        right_lip_idx = half + right_lip_rel
        right_lip = float(cup_high[right_lip_idx])

        duration = right_lip_idx - left_lip_idx
        if not (_CUP_MIN_BARS <= duration <= _CUP_MAX_DUR):
            continue

        bottom = float(cup_low[left_lip_idx : right_lip_idx + 1].min())
        if left_lip <= 0 or bottom <= 0:
            continue
        depth = (left_lip - bottom) / left_lip
        if not (_CUP_MIN_DEPTH <= depth <= _CUP_MAX_DEPTH):
            continue
        if abs(right_lip - left_lip) / left_lip > _CUP_LIP_TOL:
            continue

        handle_high = high[cup_end:]
        handle_low = low[cup_end:]
        handle_top = float(handle_high.max())
        handle_bottom = float(handle_low.min())
        if handle_top <= 0:
            continue
        handle_depth = (handle_top - handle_bottom) / handle_top
        if not (_HANDLE_MIN_DEPTH <= handle_depth <= _HANDLE_MAX_DEPTH):
            continue
        # Handle must sit below the right lip and above the cup's midpoint.
        cup_mid = (right_lip + bottom) / 2.0
        if handle_top > right_lip * 1.01 or handle_bottom < cup_mid:
            continue

        base_height = right_lip - bottom
        confidence = 60.0
        # Symmetric cups (left/right lips close) and shallower handles score higher.
        confidence += 10.0 * (1.0 - abs(right_lip - left_lip) / left_lip / _CUP_LIP_TOL)
        confidence += 10.0 if handle_depth < 0.10 else 0.0
        confidence = min(100.0, max(60.0, confidence))

        return PatternMatch(
            detected=True,
            pattern_name=name,
            confidence=confidence,
            breakout_level=right_lip,
            base_start_idx=len(df) - n + left_lip_idx,
            base_end_idx=len(df) - 1,
            base_height=float(base_height),
            notes={
                "left_lip": left_lip,
                "right_lip": right_lip,
                "bottom": bottom,
                "cup_depth": depth,
                "handle_depth": handle_depth,
                "cup_duration": duration,
            },
        )

    return _no_match(name)


# ── Flag ────────────────────────────────────────────────────────────────────


_FLAG_LOOKBACK = 60
_FLAG_MIN = 5
_FLAG_MAX = 15
_POLE_MIN = 5
_POLE_MAX = 19
_POLE_MIN_MOVE = 0.15     # pole is a >=15% move
_FLAG_MAX_RANGE = 0.12    # flag consolidation stays within 12%
_FLAG_MAX_DRIFT = 0.10    # flag may retrace up to 10% against the pole


def detect_flag(df: pd.DataFrame) -> PatternMatch:
    """Bull flag: a sharp >=15% "pole" on strong volume, then a shallow
    counter-trend consolidation (the "flag"). Breakout level is the top of
    the flag channel.
    """
    name = "flag"
    if len(df) < _POLE_MIN + _FLAG_MIN + 5:
        return _no_match(name)

    window = df.iloc[-min(_FLAG_LOOKBACK, len(df)):].reset_index(drop=True)
    close = window["close"].to_numpy()
    high = window["high"].to_numpy()
    low = window["low"].to_numpy()
    volume = window["volume"].to_numpy()
    n = len(window)

    for fl in range(_FLAG_MIN, _FLAG_MAX + 1):
        flag_start = n - fl
        if flag_start < _POLE_MIN:
            break
        flag_close = close[flag_start:]
        flag_high = high[flag_start:]
        flag_low = low[flag_start:]

        flag_top = float(flag_high.max())
        flag_bottom = float(flag_low.min())
        if flag_top <= 0:
            continue
        flag_range = (flag_top - flag_bottom) / flag_top
        if flag_range > _FLAG_MAX_RANGE:
            continue
        # Flag drifts sideways-to-down after an up pole (not making new highs).
        flag_move = (flag_close[-1] - flag_close[0]) / flag_close[0]
        if flag_move > 0.0 or flag_move < -_FLAG_MAX_DRIFT:
            continue

        for pl in range(_POLE_MIN, _POLE_MAX + 1):
            pole_start = flag_start - pl
            if pole_start < 0:
                break
            pole = close[pole_start:flag_start]
            if pole[0] <= 0:
                continue
            pole_move = (pole[-1] - pole[0]) / pole[0]
            if pole_move < _POLE_MIN_MOVE:
                continue
            # Pole should trade on above-average volume vs the prior history.
            pole_vol = float(volume[pole_start:flag_start].mean())
            prior = volume[:pole_start]
            prior_vol = float(prior.mean()) if len(prior) else pole_vol
            if pole_vol <= prior_vol:
                continue

            breakout_level = flag_top
            base_height = float(pole[-1] - pole[0])   # measured move = pole height
            confidence = 60.0
            confidence += min(20.0, (pole_move - _POLE_MIN_MOVE) / 0.15 * 20.0)
            confidence += 10.0 if flag_range < 0.06 else 0.0
            confidence = min(100.0, confidence)

            return PatternMatch(
                detected=True,
                pattern_name=name,
                confidence=confidence,
                breakout_level=breakout_level,
                base_start_idx=len(df) - n + pole_start,
                base_end_idx=len(df) - 1,
                base_height=base_height,
                notes={
                    "pole_move": pole_move,
                    "pole_len": pl,
                    "flag_len": fl,
                    "flag_range": flag_range,
                    "flag_drift": flag_move,
                },
            )

    return _no_match(name)


# ── Dispatcher ───────────────────────────────────────────────────────────


PATTERN_REGISTRY = {
    "fifty_two_week_high": detect_fifty_two_week_high_breakout,
    "darvas_box": detect_darvas_box,
    "nr7": detect_nr7,
    "inside_bar": detect_inside_bar_breakout,
    "bollinger_squeeze": detect_bollinger_squeeze,
    "ascending_triangle": detect_ascending_triangle,
    "vcp": detect_vcp,
    "cup_and_handle": detect_cup_and_handle,
    "flag": detect_flag,
}


def detect_all(df: pd.DataFrame, enabled: list[str]) -> list[PatternMatch]:
    """Run every enabled detector. Returns only detected matches, sorted by
    confidence descending so the strongest pattern leads."""
    matches: list[PatternMatch] = []
    for name in enabled:
        detector = PATTERN_REGISTRY.get(name)
        if detector is None:
            continue
        result = detector(df)
        if result.detected:
            matches.append(result)
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches
