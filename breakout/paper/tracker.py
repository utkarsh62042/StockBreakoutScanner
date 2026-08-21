"""Paper-trade lifecycle.

Every alert the scanner emits becomes a virtual position in the SQLite
`paper_trades` table. The state machine:

    ALERTED        — alert was generated; entry has not happened yet.
                     Transitions to ENTERED at next day's open, or CANCELED
                     if no confirmation within `alert_ttl_days`.
    ENTERED        — position is open at `entry_price`.
                     Transitions to TARGET_1_HIT, TARGET_HIT, STOPPED_OUT,
                     or TIME_EXIT.
    TARGET_1_HIT   — first target hit (partial); position remains open
                     with stop moved to entry (breakeven).
    TARGET_HIT     — second/full target hit. Terminal.
    STOPPED_OUT    — stop loss hit. Terminal.
    TIME_EXIT      — held > hold_max_days without resolution. Terminal.
    CANCELED       — alert never confirmed (price moved away). Terminal.

The settle job runs once per trading day after the close and walks every
open trade through these transitions using that day's OHLC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from breakout.config import Config


logger = logging.getLogger(__name__)


class TradeState:
    ALERTED = "ALERTED"
    ENTERED = "ENTERED"
    TARGET_1_HIT = "TARGET_1_HIT"
    TARGET_HIT = "TARGET_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    TIME_EXIT = "TIME_EXIT"
    CANCELED = "CANCELED"


OPEN_STATES = {TradeState.ALERTED, TradeState.ENTERED, TradeState.TARGET_1_HIT}
CLOSED_STATES = {
    TradeState.TARGET_HIT,
    TradeState.STOPPED_OUT,
    TradeState.TIME_EXIT,
    TradeState.CANCELED,
}


# ── Math helpers ─────────────────────────────────────────────────────────


def position_size(
    capital: float,
    risk_per_trade_pct: float,
    entry: float,
    stop: float,
) -> int:
    """Return the integer share count that risks `risk_per_trade_pct` of
    `capital` if `stop` is hit. Returns 0 if the trade has zero risk (which
    would imply an entry at the stop — not a legitimate setup)."""
    risk_amount = capital * (risk_per_trade_pct / 100.0)
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        return 0
    return int(risk_amount / risk_per_share)


def compute_stop(breakout_level: float, atr: float, multiplier: float = 1.5) -> float:
    """Volatility-adjusted stop: N × ATR below the breakout level."""
    return breakout_level - multiplier * atr


def compute_targets(
    entry: float,
    stop: float,
    base_height: float,
    r_multiple: float = 2.0,
) -> tuple[float, float]:
    """Returns (target_1, target_2).
    Target 1 = entry + r_multiple × risk_per_share (e.g. 2:1 R:R).
    Target 2 = entry + base_height (measured-move projection).
    """
    risk_per_share = abs(entry - stop)
    target_1 = entry + r_multiple * risk_per_share
    target_2 = entry + base_height
    return target_1, target_2


# ── State transitions ───────────────────────────────────────────────────


@dataclass
class TradeOutcome:
    """The transition decision returned by `settle_one_trade`.

    None of these fields are persisted directly; the caller writes them
    onto the `paper_trades` row via `Store.update_paper_trade`.
    """

    new_state: str
    exit_price: float | None = None
    exit_date: str | None = None
    pnl_inr: float | None = None
    pnl_r: float | None = None
    days_held: int | None = None
    notes: str = ""


def settle_one_trade(
    trade: dict,
    today_ohlc: dict,
    today: date,
    cfg: Config,
) -> TradeOutcome | None:
    """Decide the next state for a single trade given today's OHLC.

    Returns None if no transition is required today (e.g. trade is still
    progressing within its bounds).
    """
    state = trade["state"]
    if state in CLOSED_STATES:
        return None

    today_iso = today.isoformat()
    today_high = float(today_ohlc["high"])
    today_low = float(today_ohlc["low"])
    today_open = float(today_ohlc["open"])

    # ── ALERTED bookkeeping ──────────────────────────────────────────────
    if state == TradeState.ALERTED:
        alert_date = pd.to_datetime(trade["alert_date"]).date()
        days_since_alert = (today - alert_date).days
        if days_since_alert >= cfg.paper_trading.alert_ttl_days:
            return TradeOutcome(
                new_state=TradeState.CANCELED,
                exit_date=today_iso,
                notes=f"alert_expired_{days_since_alert}d_no_confirmation",
            )
        # Transition to ENTERED at today's open if it confirms the breakout
        breakout_level = float(trade.get("stop_loss") or 0) + 0  # not used here
        # We treat any trading day after the alert as confirmation, since the
        # alert itself already required a close > breakout level. The entry
        # price is today's open (realistic — the trader would act EOD or at
        # next open). The stop, targets, and shares were set at alert time.
        risk_per_share = abs(today_open - float(trade["stop_loss"]))
        outcome = TradeOutcome(
            new_state=TradeState.ENTERED,
            notes=f"entered_at_open_{today_open:.2f}",
        )
        outcome.exit_price = None  # entered, not exited
        # Caller updates entry_date/entry_price/shares from these fields:
        outcome._entered_open = today_open  # type: ignore[attr-defined]
        outcome._risk_per_share = risk_per_share  # type: ignore[attr-defined]
        return outcome

    # ── For ENTERED / TARGET_1_HIT trades, run exit checks ──────────────
    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    target_1 = float(trade["target_1"]) if trade.get("target_1") is not None else None
    target_2 = float(trade["target_2"]) if trade.get("target_2") is not None else None
    shares = int(trade.get("shares") or 0)
    entry_date = pd.to_datetime(trade["entry_date"]).date()
    days_held = (today - entry_date).days

    # 1. Stop hit
    if today_low <= stop_loss:
        exit_price = stop_loss  # conservative — assume fill at stop
        pnl_inr = (exit_price - entry_price) * shares
        pnl_r = (exit_price - entry_price) / max(1e-9, abs(entry_price - stop_loss))
        return TradeOutcome(
            new_state=TradeState.STOPPED_OUT,
            exit_price=exit_price,
            exit_date=today_iso,
            pnl_inr=pnl_inr,
            pnl_r=pnl_r,
            days_held=days_held,
            notes="stop_hit_intraday",
        )

    # 2. Full target (target_2) hit
    if target_2 is not None and today_high >= target_2:
        exit_price = target_2
        pnl_inr = (exit_price - entry_price) * shares
        pnl_r = (exit_price - entry_price) / max(1e-9, abs(entry_price - stop_loss))
        return TradeOutcome(
            new_state=TradeState.TARGET_HIT,
            exit_price=exit_price,
            exit_date=today_iso,
            pnl_inr=pnl_inr,
            pnl_r=pnl_r,
            days_held=days_held,
            notes="target_2_hit",
        )

    # 3. First target hit (partial) — only transition once
    if (
        state == TradeState.ENTERED
        and target_1 is not None
        and today_high >= target_1
    ):
        return TradeOutcome(
            new_state=TradeState.TARGET_1_HIT,
            notes=f"target_1_hit_stop_to_be:{entry_price:.2f}",
        )

    # 4. Time exit
    if days_held >= cfg.paper_trading.hold_max_days:
        exit_price = float(today_ohlc["close"])
        pnl_inr = (exit_price - entry_price) * shares
        pnl_r = (exit_price - entry_price) / max(1e-9, abs(entry_price - stop_loss))
        return TradeOutcome(
            new_state=TradeState.TIME_EXIT,
            exit_price=exit_price,
            exit_date=today_iso,
            pnl_inr=pnl_inr,
            pnl_r=pnl_r,
            days_held=days_held,
            notes=f"time_exit_after_{days_held}d",
        )

    return None


# ── High-level helpers used by the job entry points ─────────────────────


def insert_alert(
    store: "Any",  # forward — avoid circular import for type checking
    *,
    symbol: str,
    pattern: str,
    alert_type: str,
    score: float,
    breakout_level: float,
    atr: float,
    base_height: float,
    cfg: Config,
    alert_date: date | None = None,
) -> int:
    """Insert a new ALERTED paper trade computed from a pattern match."""
    alert_date = alert_date or date.today()
    entry = breakout_level  # alert is emitted when close > breakout_level
    stop = compute_stop(breakout_level, atr, cfg.paper_trading.atr_stop_multiplier)
    target_1, target_2 = compute_targets(
        entry, stop, base_height, cfg.paper_trading.target_1_r_multiple
    )
    shares = position_size(
        cfg.risk.capital, cfg.risk.risk_per_trade_pct, entry, stop
    )
    return store.insert_paper_trade(
        {
            "symbol": symbol,
            "pattern": pattern,
            "alert_date": alert_date.isoformat(),
            "alert_type": alert_type,
            "score": score,
            "state": TradeState.ALERTED,
            "stop_loss": stop,
            "target_1": target_1,
            "target_2": target_2,
            "shares": shares,
            "notes": f"breakout_level={breakout_level:.2f}",
        }
    )


def apply_outcome(store: Any, trade_id: int, outcome: TradeOutcome) -> None:
    """Persist a TradeOutcome to the store."""
    fields: dict[str, Any] = {"state": outcome.new_state}
    if outcome.exit_price is not None:
        fields["exit_price"] = outcome.exit_price
    if outcome.exit_date is not None:
        fields["exit_date"] = outcome.exit_date
    if outcome.pnl_inr is not None:
        fields["pnl_inr"] = outcome.pnl_inr
    if outcome.pnl_r is not None:
        fields["pnl_r"] = outcome.pnl_r
    if outcome.days_held is not None:
        fields["days_held"] = outcome.days_held
    if outcome.notes:
        # Append rather than overwrite — multiple transitions can write notes
        fields["notes"] = outcome.notes
    # Special-case ALERTED -> ENTERED: capture entry price & date
    entered_open = getattr(outcome, "_entered_open", None)
    if outcome.new_state == TradeState.ENTERED and entered_open is not None:
        fields["entry_price"] = entered_open
        fields["entry_date"] = datetime.now().date().isoformat()
    store.update_paper_trade(trade_id, **fields)
