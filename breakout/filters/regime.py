"""Market-regime assessment — the largest remaining accuracy lever.

Breakout strategies concentrate nearly all their losses in risk-off regimes:
the same setup that works in a broad advance fails repeatedly when the index is
below a falling 200DMA and participation is narrowing. Until now the scanner
logged the VIX regime and proceeded regardless, which is advisory, not a gate.

Three independent reads of the same question — *is the market paying for
breakouts right now?* — each voting -1 / 0 / +1:

    nifty    close vs a rising/falling 200DMA (`mood.index_trend`)
    breadth  % of the universe above its own 50DMA — participation, which
             turns before the index does, and is free: the morning scan
             already reads every symbol's history for the RS pre-pass
    vix      India VIX vs its own 20-day average (`mood.vix_mood`)

The sum lands in [-3, +3] and maps to a label and a **multiplier on
`risk_per_trade_pct`**. A multiplier rather than an on/off switch, deliberately:

  * A hard gate discards the observations that would tell us whether the gate
    was right. Sizing down keeps the alert, the paper trade, and the
    `alert_features` row — so `regime` and `breadth_pct` accumulate against
    realised `pnl_r` and the thresholds below can be set from results instead of
    from the priors they are today.
  * Regime is a continuum. Half size in a deteriorating tape is a closer model
    of what a discretionary trader actually does than a binary halt.

Nothing here ever raises size above baseline: there is no evidence yet that
risk-on days deserve more than the configured risk per trade, and inventing
upside is how a filter becomes a leverage knob.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from breakout.filters.mood import index_trend


@dataclass(frozen=True)
class RegimeAssessment:
    """What the three votes concluded, and the sizing that follows."""

    label: str                  # "risk_on" | "neutral" | "risk_off"
    score: int                  # -3..+3, the sum of the three votes
    risk_multiplier: float      # applied to risk_per_trade_pct at entry
    nifty_trend: str            # "up" | "flat" | "down"
    breadth_pct: float | None   # % of universe above its own 50DMA
    vix_mood: str               # "risk_on" | "neutral" | "risk_off"
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        breadth = f"{self.breadth_pct:.0f}%" if self.breadth_pct is not None else "n/a"
        return (
            f"{self.label} (score {self.score:+d}, size x{self.risk_multiplier:.2f}) "
            f"— nifty={self.nifty_trend}, breadth={breadth}, vix={self.vix_mood}"
        )


def pct_above_sma(closes: pd.Series, period: int = 50) -> bool | None:
    """Is the last close above its own `period`-day SMA? None if too short.

    Deliberately a plain mean over the last `period` bars rather than a full
    indicator pass — this runs once per symbol over the whole universe.
    """
    s = pd.to_numeric(closes, errors="coerce").dropna()
    if len(s) < period:
        return None
    return float(s.iloc[-1]) > float(s.tail(period).mean())


def market_breadth(above: int, total: int) -> float | None:
    """Percent of measurable symbols above their own SMA. None if none were."""
    if total <= 0:
        return None
    return above / total * 100.0


def _vote_breadth(breadth_pct: float | None, up_pct: float, down_pct: float) -> int:
    if breadth_pct is None:
        return 0
    if breadth_pct >= up_pct:
        return 1
    if breadth_pct <= down_pct:
        return -1
    return 0


def _vote_trend(trend: str) -> int:
    return {"up": 1, "down": -1}.get(trend, 0)


def _vote_vix(mood: str) -> int:
    return {"risk_on": 1, "risk_off": -1}.get(mood, 0)


def assess_regime(
    nifty_close: pd.Series | None,
    breadth_pct: float | None,
    vix_mood_label: str,
    cfg,
) -> RegimeAssessment:
    """Combine the three votes into a label and a sizing multiplier.

    `cfg` is a `RegimeConfig`. With `enabled: false` this still reports the
    regime — the observation is worth logging either way — but pins the
    multiplier at 1.0 so sizing is untouched.
    """
    if nifty_close is not None and len(nifty_close):
        trend = index_trend(
            pd.DataFrame({"close": list(nifty_close)}),
            sma_period=cfg.nifty_sma,
            slope_lookback=cfg.nifty_slope_lookback,
        )
    else:
        # No index data is not evidence of a bad tape — stay neutral rather
        # than penalising every trade because one fetch failed.
        trend = "flat"

    votes = {
        "nifty": _vote_trend(trend),
        "breadth": _vote_breadth(breadth_pct, cfg.breadth_up_pct, cfg.breadth_down_pct),
        "vix": _vote_vix(vix_mood_label),
    }
    score = sum(votes.values())

    if score >= 2:
        label = "risk_on"
    elif score <= -2:
        label = "risk_off"
    else:
        label = "neutral"

    if label == "risk_off":
        # All three negative is a different animal from two of three.
        multiplier = (
            cfg.severe_multiplier if score == -3 else cfg.risk_off_multiplier
        )
    else:
        multiplier = 1.0
    if not cfg.enabled:
        multiplier = 1.0

    reasons = [f"{name}={v:+d}" for name, v in votes.items()]
    return RegimeAssessment(
        label=label,
        score=score,
        risk_multiplier=multiplier,
        nifty_trend=trend,
        breadth_pct=breadth_pct,
        vix_mood=vix_mood_label,
        reasons=reasons,
    )
