"""Swing-high / swing-low pivot detection.

This module is the foundation that every chart pattern depends on. A bug
here propagates to every detector downstream, so the corresponding
`tests/test_pivots.py` is intentionally exhaustive.

Definitions (strict):
    - A **swing high** at index `i` is a bar where `high[i]` is strictly greater
      than `high[i-k]` and `high[i+k]` for all k in 1..N.
    - A **swing low** at index `i` is a bar where `low[i]` is strictly less
      than `low[i-k]` and `low[i+k]` for all k in 1..N.

Strictness intentionally rejects "flat-top" candidates where two or more bars
share the same extreme value in the lookback window. This is the conservative
choice — we'd rather miss a borderline pivot than count a flat resistance as
a clean swing point and feed a noisy signal into the pattern layer.

Confirmation lag: a pivot at index i requires N future bars to confirm, so
the most recent N bars cannot be classified yet. Pattern detectors must
account for this lag.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class PivotKind(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class Pivot:
    """A single confirmed pivot.

    Attributes:
        index: Positional index into the source DataFrame (0-based).
        price: The pivot extreme value (high for HIGH, low for LOW).
        kind: HIGH or LOW.
        strength: Number of consecutive bars on the weaker side that this
            pivot strictly dominates (looking beyond the confirmation
            window of N). Always >= the N used to confirm the pivot.
    """

    index: int
    price: float
    kind: PivotKind
    strength: int


@dataclass(frozen=True)
class Pivots:
    """Container for the two pivot series from a single DataFrame scan."""

    highs: list[Pivot]
    lows: list[Pivot]

    def high_indices(self) -> list[int]:
        return [p.index for p in self.highs]

    def low_indices(self) -> list[int]:
        return [p.index for p in self.lows]

    def last_n_highs(self, n: int) -> list[Pivot]:
        return self.highs[-n:] if len(self.highs) >= n else self.highs

    def last_n_lows(self, n: int) -> list[Pivot]:
        return self.lows[-n:] if len(self.lows) >= n else self.lows


_REQUIRED_COLS = ("high", "low")


def _validate_df(df: pd.DataFrame) -> None:
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"DataFrame missing required columns: {missing}. "
            f"Got columns: {list(df.columns)}"
        )


def _compute_strength(df: pd.DataFrame, idx: int, kind: PivotKind) -> int:
    """Walks outward from a confirmed pivot until the dominance breaks.

    Returns min(left_run, right_run) — the conservative dominance measure.
    A pivot that dominates 30 bars on the left but only 5 on the right is
    really a 5-bar pivot in terms of resistance/support quality.
    """
    col = "high" if kind == PivotKind.HIGH else "low"
    val = df[col].iloc[idx]
    n = len(df)

    if kind == PivotKind.HIGH:
        def dominates(other: float) -> bool:
            return other < val
    else:
        def dominates(other: float) -> bool:
            return other > val

    left = 0
    i = idx - 1
    while i >= 0 and dominates(df[col].iloc[i]):
        left += 1
        i -= 1

    right = 0
    i = idx + 1
    while i < n and dominates(df[col].iloc[i]):
        right += 1
        i += 1

    return min(left, right)


def find_pivots(df: pd.DataFrame, n: int = 5) -> Pivots:
    """Identify confirmed swing highs and swing lows.

    Args:
        df: OHLCV DataFrame with lowercase 'high' and 'low' columns.
        n: Bars required on each side. Default 5 (good for daily swing trades);
           use 3 for faster confirmation, 10 for higher-conviction major pivots.

    Returns:
        Pivots container with separate lists of HIGH and LOW pivots, in
        chronological (ascending-index) order.

    The most recent `n` bars cannot be confirmed; they may yet turn out to
    be pivots once enough future bars arrive. Pattern detectors should not
    treat the absence of a recent pivot as a meaningful signal.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    _validate_df(df)
    if len(df) < 2 * n + 1:
        return Pivots(highs=[], lows=[])

    highs: list[Pivot] = []
    lows: list[Pivot] = []
    high_arr = df["high"].to_numpy()
    low_arr = df["low"].to_numpy()
    length = len(df)

    for i in range(n, length - n):
        window_h = high_arr[i - n : i + n + 1]
        window_l = low_arr[i - n : i + n + 1]
        h_i = high_arr[i]
        l_i = low_arr[i]

        # Strict pivot: must be the unique max (or min) in the window
        if h_i == window_h.max() and (window_h == h_i).sum() == 1:
            highs.append(
                Pivot(
                    index=i,
                    price=float(h_i),
                    kind=PivotKind.HIGH,
                    strength=_compute_strength(df, i, PivotKind.HIGH),
                )
            )
        if l_i == window_l.min() and (window_l == l_i).sum() == 1:
            lows.append(
                Pivot(
                    index=i,
                    price=float(l_i),
                    kind=PivotKind.LOW,
                    strength=_compute_strength(df, i, PivotKind.LOW),
                )
            )

    return Pivots(highs=highs, lows=lows)
