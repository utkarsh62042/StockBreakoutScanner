"""Paper-trade lifecycle.

Every alert the scanner emits becomes a virtual position in the SQLite
`paper_trades` table. The state machine:

    ENTERED        — position is open at `entry_price`, the price at the
                     3:00 PM pre-close scan that confirmed the breakout. This
                     is the price the trader actually pays, entering in the
                     3:00–3:20 PM window on the confirmation day itself.
                     Transitions to TARGET_1_HIT, TARGET_HIT, STOPPED_OUT,
                     or TIME_EXIT.
    TARGET_1_HIT   — first target hit (partial); position remains open
                     with stop moved to entry (breakeven).
    TARGET_HIT     — second/full target hit. Terminal.
    STOPPED_OUT    — stop loss hit. Terminal.
    TIME_EXIT      — held > hold_max_days without resolution. Terminal.
    CANCELED       — terminal. Legacy: no longer produced, since a trade is
                     opened at confirmation rather than waiting on one.
    ALERTED        — legacy state from the old next-day-open entry model. No
                     longer produced; rows in it are skipped by the settle.

The settle job runs once per trading day after the close and walks every
open trade through these transitions using that day's OHLC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
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
    """Volatility-adjusted stop: N × ATR below the breakout level.

    Deliberately anchored to the breakout level, NOT to the entry price. The
    level is the structure whose violation invalidates the setup; anchoring
    the stop to an entry that printed well above it would place the stop
    above the level, so an ordinary retest would knock the trade out.

    Everything that depends on what you actually paid — risk per share,
    position size, targets, R:R — uses the entry price instead.
    """
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

    `entry` is the 3 PM pre-close price, so the quoted R:R is the one the
    trader actually gets rather than the one an idealised fill at the
    breakout level would have got.
    """
    risk_per_share = abs(entry - stop)
    target_1 = entry + r_multiple * risk_per_share
    target_2 = entry + base_height
    return target_1, target_2


# ── Day-by-day progress ─────────────────────────────────────────────────
# Two derived columns on `paper_trades` let the user see how a position has
# behaved since entry without opening a chart:
#
#   days_in_trade  "D3"                        — trading days elapsed since entry
#   daily_moves    "D1:1.0%,D2:3.7%,D3:-2.3%"  — that single day's move
#
# The entry day is D0 (the trade was just opened — no movement yet, so it
# carries no entry in `daily_moves`). Each figure is one day's own move, not a
# running total: D1 is `entry_price` → D1's close, D2 is D1's close → D2's
# close, and so on. So D3:-2.3% means the stock fell 2.3% on day three. The
# whole string is recomputed from cached bars on each settle rather than
# appended to, so a re-run of the job is idempotent.


def compute_daily_progress(
    entry_date: Any,
    entry_price: float | None,
    prices: pd.DataFrame,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Return `(days_in_trade, daily_moves)` for one trade.

    `prices` is a date-indexed OHLCV frame (as returned by
    `Store.read_prices`) that must cover the entry day onwards. Bars after
    `today` are ignored. On the entry day itself returns `("D0", None)`.
    Returns `(None, None)` when the trade hasn't been entered or no bar on/after
    the entry day is cached yet.
    """
    if entry_price is None or entry_date is None or prices.empty:
        return None, None
    try:
        entry = pd.to_datetime(entry_date).date()
        entry_px = float(entry_price)
    except (TypeError, ValueError):
        return None, None
    if entry_px <= 0:
        return None, None

    bars = prices.sort_index()
    days = pd.DatetimeIndex(bars.index).date
    mask = days >= entry
    if today is not None:
        mask = mask & (days <= today)
    bars = bars[mask]
    if bars.empty:
        return None, None

    closes = pd.to_numeric(bars["close"], errors="coerce").dropna()
    if closes.empty:
        return None, None

    # The entry-day bar is D0 — the position has no elapsed movement on the day
    # it was opened, so that bar only supplies D1's baseline (`entry_price`, the
    # price actually paid, rather than D0's close).
    moves: list[str] = []
    prev = entry_px
    for n, close in enumerate(closes.tolist()[1:], start=1):
        moves.append(f"D{n}:{(close - prev) / prev * 100.0:.1f}%")
        prev = close
    if not moves:
        return "D0", None
    return f"D{len(moves)}", ",".join(moves)


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

    if state == TradeState.ALERTED:
        # Legacy rows from the old next-day-open entry model. Trades are now
        # opened as ENTERED at the 3 PM confirmation price, so nothing creates
        # this state any more and there is no entry_price to settle against.
        logger.warning(
            f"trade {trade.get('id')} ({trade.get('symbol')}) is in legacy "
            "ALERTED state — skipping; re-run the scan to open it fresh"
        )
        return None

    today_iso = today.isoformat()
    today_high = float(today_ohlc["high"])
    today_low = float(today_ohlc["low"])

    # ── For ENTERED / TARGET_1_HIT trades, run exit checks ──────────────
    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    target_1 = float(trade["target_1"]) if trade.get("target_1") is not None else None
    target_2 = float(trade["target_2"]) if trade.get("target_2") is not None else None
    shares = int(trade.get("shares") or 0)
    entry_date = pd.to_datetime(trade["entry_date"]).date()
    days_held = (today - entry_date).days

    # Entry day (D0): we bought at ~3 PM, so the day's high and low are mostly
    # from bars that printed BEFORE we were in the position. Testing them would
    # manufacture stops and targets we could never have hit. First exit check
    # is the next session.
    if days_held <= 0:
        return None

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
    entry_price: float,
    atr: float,
    base_height: float,
    cfg: Config,
    alert_date: date | None = None,
) -> int:
    """Open a paper trade at the 3 PM pre-close confirmation price.

    `entry_price` is that day's price at the moment the scan confirmed the
    breakout — the price the trader pays entering in the 3:00–3:20 PM window.
    The trade goes straight to ENTERED: there is no waiting period, so every
    number on the row (risk per share, position size, both targets, and the
    R:R they imply) is measured from a fill that is actually obtainable.

    `breakout_level` still anchors the stop — see `compute_stop`.
    """
    alert_date = alert_date or date.today()
    entry = float(entry_price)
    stop = compute_stop(breakout_level, atr, cfg.paper_trading.atr_stop_multiplier)
    target_1, target_2 = compute_targets(
        entry, stop, base_height, cfg.paper_trading.target_1_r_multiple
    )
    shares = position_size(
        cfg.risk.capital, cfg.risk.risk_per_trade_pct, entry, stop
    )
    # How far above the level we had to pay. A large extension means the move
    # ran away intraday: risk per share is wider, so `shares` is already
    # smaller, but it is worth seeing on the row.
    extension_pct = (
        (entry - breakout_level) / breakout_level * 100.0 if breakout_level > 0 else 0.0
    )
    return store.insert_paper_trade(
        {
            "symbol": symbol,
            "pattern": pattern,
            "alert_date": alert_date.isoformat(),
            "alert_type": alert_type,
            "score": score,
            "state": TradeState.ENTERED,
            "entry_date": alert_date.isoformat(),
            "entry_price": entry,
            "stop_loss": stop,
            "target_1": target_1,
            "target_2": target_2,
            "shares": shares,
            "notes": (
                f"breakout_level={breakout_level:.2f},"
                f"entry_at_preclose={entry:.2f},"
                f"extension={extension_pct:+.2f}%"
            ),
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
    # entry_price / entry_date are written once, at insert time — a trade is
    # already ENTERED when it is created, so no transition sets them.
    store.update_paper_trade(trade_id, **fields)
