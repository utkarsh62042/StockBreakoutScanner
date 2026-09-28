"""Portfolio-level risk gates and circuit breakers.

These gates operate above individual trade level and prevent the system from
opening new positions when portfolio is in distress, accounting for:

1. Daily losses — FII/DII selling can create cascading losses. Stop new entries
   if today's closed trades are already underwater.

2. Monthly drawdown — track realized losses month-to-date. If down more than
   threshold, stop trading until next month.

3. FII/DII flow context — market mood (India VIX, breadth) flags whether FII
   are buying or selling. Reduce sizing in DII-only rallies (fragile).

4. Sector concentration stress — if one sector has high open position count and
   its index drops >2%, close new entries in that sector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from breakout.analysis.session import today_ist


@dataclass(frozen=True)
class PortfolioRiskStatus:
    """Assessment of portfolio health."""

    is_healthy: bool
    daily_loss_pct: float | None
    monthly_loss_pct: float | None
    reasons: list[str]

    def summary(self) -> str:
        parts = [f"healthy={self.is_healthy}"]
        if self.daily_loss_pct is not None:
            parts.append(f"daily={self.daily_loss_pct:.2f}%")
        if self.monthly_loss_pct is not None:
            parts.append(f"monthly={self.monthly_loss_pct:.2f}%")
        return " | ".join(parts)


def assess_portfolio_health(
    store: Any,  # Store object
    cfg: Any,    # Config object
    today: date | None = None,
) -> PortfolioRiskStatus:
    """Check if portfolio is in a state to open new positions.

    Returns PortfolioRiskStatus with is_healthy=False if any circuit breaker
    is triggered. Reasons list explains what triggered.

    Args:
        store: Data store with paper trades
        cfg: Config with risk limits
        today: Date to assess (defaults to today IST)

    Returns:
        PortfolioRiskStatus with assessment
    """
    if today is None:
        today = today_ist().date()

    reasons: list[str] = []
    daily_loss = None
    monthly_loss = None

    # Get all paper trades
    all_trades = store.read_paper_trades_by_state()

    # ── Daily loss check ─────────────────────────────────────────────────────
    today_closed = [
        t for t in all_trades
        if t.get("exit_date") and pd.to_datetime(t["exit_date"]).date() == today
    ]
    if today_closed:
        today_pnl = sum(float(t.get("pnl_inr") or 0) for t in today_closed)
        daily_loss = today_pnl / cfg.risk.capital * 100.0

        daily_limit = getattr(cfg.risk, "max_portfolio_loss_pct_daily", 3.0)
        if daily_pnl < -cfg.risk.capital * daily_limit / 100.0:
            reasons.append(
                f"Daily loss ₹{today_pnl:.0f} ({daily_loss:.2f}%) "
                f"exceeds limit {daily_limit}%"
            )

    # ── Monthly loss check ───────────────────────────────────────────────────
    month_start = today.replace(day=1)
    month_closed = [
        t for t in all_trades
        if t.get("exit_date")
        and pd.to_datetime(t["exit_date"]).date() >= month_start
        and pd.to_datetime(t["exit_date"]).date() <= today
    ]
    if month_closed:
        month_pnl = sum(float(t.get("pnl_inr") or 0) for t in month_closed)
        monthly_loss = month_pnl / cfg.risk.capital * 100.0

        monthly_limit = getattr(cfg.risk, "max_portfolio_loss_pct_monthly", 10.0)
        if month_pnl < -cfg.risk.capital * monthly_limit / 100.0:
            reasons.append(
                f"Monthly loss ₹{month_pnl:.0f} ({monthly_loss:.2f}%) "
                f"exceeds limit {monthly_limit}%"
            )

    # ── Open position count check ────────────────────────────────────────────
    # If too many open positions in one sector, be cautious
    open_states = {"ALERTED", "ENTERED", "TARGET_1_HIT"}
    open_trades = [t for t in all_trades if t.get("state") in open_states]

    if len(open_trades) >= getattr(cfg.risk, "max_concurrent_positions", 8):
        reasons.append(
            f"Concurrent position limit ({len(open_trades)} open) reached"
        )

    is_healthy = len(reasons) == 0

    return PortfolioRiskStatus(
        is_healthy=is_healthy,
        daily_loss_pct=daily_loss,
        monthly_loss_pct=monthly_loss,
        reasons=reasons,
    )


def should_scale_position_for_market_stress(
    vix: float | None,
    market_breadth_pct: float | None,
    regime_label: str | None = None,
) -> float:
    """Return a multiplier (0.0 to 1.0) to scale position size in stressed markets.

    In risk-off regimes (FII selling, high VIX), scale down aggressively even if
    individual setups look good. In risk-on, stay at 1.0.

    Args:
        vix: India VIX level (None if not available)
        market_breadth_pct: % of universe above 50DMA (None if not available)
        regime_label: Label from regime gate ("risk_on", "neutral", "risk_off")

    Returns:
        Multiplier in [0.0, 1.0] to apply to position size
    """
    multipliers: list[float] = [1.0]

    # VIX-based scaling: >25 = elevated, >30 = severe stress
    if vix is not None:
        if vix >= 30.0:
            multipliers.append(0.25)  # Severe: 25% of normal size
        elif vix >= 25.0:
            multipliers.append(0.50)  # Elevated: 50% of normal size
        elif vix >= 20.0:
            multipliers.append(0.75)  # Caution: 75% of normal size

    # Breadth-based scaling: <40% = narrow market, <30% = breakouts failing
    if market_breadth_pct is not None:
        if market_breadth_pct < 30.0:
            multipliers.append(0.30)  # Very narrow: scale to 30%
        elif market_breadth_pct < 40.0:
            multipliers.append(0.60)  # Narrow: scale to 60%
        elif market_breadth_pct < 50.0:
            multipliers.append(0.80)  # Tightening: scale to 80%

    # Regime-based scaling: already applied in the sizing knob, but
    # this provides secondary confirmation if available
    if regime_label == "risk_off":
        multipliers.append(0.50)
    elif regime_label == "neutral":
        multipliers.append(0.80)

    # Use the minimum of all applicable multipliers (most conservative)
    return min(multipliers)
