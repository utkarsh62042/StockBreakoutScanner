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
from breakout.paper.costs import settle_pnl


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


def max_position_value(capital: float, max_concurrent_positions: int) -> float:
    """The most one position may be worth, so a full book fits the account.

    Derived from `max_concurrent_positions` rather than configured separately:
    "8 concurrent positions" only means anything if eight of them fit, so at
    ₹2,00,000 and 8 slots a position is capped at ₹25,000 and a full book is
    exactly 100% of capital. One number, no way for two knobs to disagree.
    """
    return capital / max(1, max_concurrent_positions)


def position_size(
    capital: float,
    risk_per_trade_pct: float,
    entry: float,
    stop: float,
    max_value: float | None = None,
) -> int:
    """Return the integer share count that risks `risk_per_trade_pct` of
    `capital` if `stop` is hit, subject to `max_value` of exposure.

    Returns 0 if the trade has zero risk (which would imply an entry at the
    stop — not a legitimate setup).

    **`max_value` is not optional in practice.** Pure risk-based sizing has no
    notion of what a position *costs*, only of what it can lose, and the two
    diverge violently when the stop is tight:

        position value = risk_amount / (stop distance as a fraction of price)

    so a 2.5% stop with 2.5% capital risk buys exactly 100% of the account in a
    single trade, and a tighter stop buys more than the account holds. Measured
    on real setups before this cap existed, positions ran 62-133% of capital
    each — on a strategy that intends to hold eight at once. The share counts
    the scanner printed were not ones the account could have bought.
    """
    risk_amount = capital * (risk_per_trade_pct / 100.0)
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        return 0
    shares = int(risk_amount / risk_per_share)
    if max_value is not None and entry > 0:
        shares = min(shares, int(max_value / entry))
    return shares


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


# ── Excursions ──────────────────────────────────────────────────────────
# `max_favorable` / `max_adverse` — the best and worst the position ever got to,
# in R, at any point after entry. They have been in the schema and documented in
# GUIDE § 12 since the start and were never written, which is a shame: they are
# the cheapest way to find out whether the stop and target are in the right
# place, and neither question is answerable from the realised P&L alone.
#
#   max_favorable 3.5R on a trade that exited at +2R  -> the target is too near
#   max_adverse  -0.9R on a trade that went on to win -> the stop is too tight
#
# Measured in R (not rupees) so trades of different sizes compare directly, and
# recomputed from cached bars on each settle rather than accumulated, so re-runs
# stay idempotent.


def compute_excursions(
    entry_price: float | None,
    stop_loss: float | None,
    prices: pd.DataFrame,
    entry_date: Any,
    today: date | None = None,
) -> tuple[float | None, float | None]:
    """Return `(max_favorable_r, max_adverse_r)` for one trade.

    Bars are taken strictly *after* the entry day: the entry bar's own high and
    low mostly printed before the 3 PM fill, exactly as in `settle_one_trade`.
    Returns `(None, None)` when there is nothing to measure yet.
    """
    if entry_price is None or stop_loss is None or entry_date is None or prices.empty:
        return None, None
    try:
        entry_px = float(entry_price)
        stop = float(stop_loss)
        entry_day = pd.to_datetime(entry_date).date()
    except (TypeError, ValueError):
        return None, None
    risk_per_share = abs(entry_px - stop)
    if entry_px <= 0 or risk_per_share <= 0:
        return None, None

    bars = prices.sort_index()
    days = pd.DatetimeIndex(bars.index).date
    mask = days > entry_day
    if today is not None:
        mask = mask & (days <= today)
    bars = bars[mask]
    if bars.empty:
        return None, None

    highs = pd.to_numeric(bars["high"], errors="coerce").dropna()
    lows = pd.to_numeric(bars["low"], errors="coerce").dropna()
    if highs.empty or lows.empty:
        return None, None

    favorable = (float(highs.max()) - entry_px) / risk_per_share
    adverse = (float(lows.min()) - entry_px) / risk_per_share
    # Clamp at 0 in each direction: a position that never traded above entry has
    # no favorable excursion, and "max adverse +0.3R" would be nonsense.
    return max(0.0, favorable), min(0.0, adverse)


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
    pnl_inr: float | None = None        # net of transaction costs
    pnl_r: float | None = None          # net of transaction costs
    gross_pnl_inr: float | None = None  # before costs, so the drag is visible
    costs_inr: float | None = None
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

    def _close_at(exit_price: float, state: str, notes: str) -> TradeOutcome:
        gross, costs_inr, net, net_r = settle_pnl(
            entry_price, exit_price, shares, stop_loss, cfg.costs
        )
        return TradeOutcome(
            new_state=state,
            exit_price=exit_price,
            exit_date=today_iso,
            pnl_inr=net,
            pnl_r=net_r,
            gross_pnl_inr=gross,
            costs_inr=costs_inr,
            days_held=days_held,
            notes=notes,
        )

    # 1. Stop hit. A fill exactly at the stop is only available if the stop was
    # reached during the session; if the bar OPENED below it the position gaps
    # through and the real fill is the open. Gap-downs on bad news are the main
    # source of the fat left tail on NSE, and assuming a fill at the stop
    # overstates R on precisely the trades that hurt most.
    if today_low <= stop_loss:
        today_open = float(today_ohlc["open"])
        exit_price = min(stop_loss, today_open)
        gapped = today_open < stop_loss
        return _close_at(
            exit_price,
            TradeState.STOPPED_OUT,
            f"stop_gap_open:{exit_price:.2f}" if gapped else "stop_hit_intraday",
        )

    # 2. Full target (target_2) hit. No mirror-image fix needed here: filling at
    # the target when the bar opened above it understates the gain, which is the
    # conservative direction.
    if target_2 is not None and today_high >= target_2:
        return _close_at(target_2, TradeState.TARGET_HIT, "target_2_hit")

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
        return _close_at(
            float(today_ohlc["close"]),
            TradeState.TIME_EXIT,
            f"time_exit_after_{days_held}d",
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
    risk_multiplier: float = 1.0,
) -> int:
    """Open a paper trade at the 3 PM pre-close confirmation price.

    `entry_price` is that day's price at the moment the scan confirmed the
    breakout — the price the trader pays entering in the 3:00–3:20 PM window.
    The trade goes straight to ENTERED: there is no waiting period, so every
    number on the row (risk per share, position size, both targets, and the
    R:R they imply) is measured from a fill that is actually obtainable.

    `breakout_level` still anchors the stop — see `compute_stop`.

    `risk_multiplier` scales risk per trade for the market regime (see
    `filters.regime`). It touches size only: the stop, the targets and the R:R
    they imply are properties of the setup, not of the tape, so a half-size
    position in a risk-off regime is still a 1R loss if it stops out — it just
    costs half as many rupees.
    """
    alert_date = alert_date or date.today()
    entry = float(entry_price)
    stop = compute_stop(breakout_level, atr, cfg.paper_trading.atr_stop_multiplier)
    target_1, target_2 = compute_targets(
        entry, stop, base_height, cfg.paper_trading.target_1_r_multiple
    )
    shares = position_size(
        cfg.risk.capital,
        cfg.risk.risk_per_trade_pct * risk_multiplier,
        entry,
        stop,
        max_value=max_position_value(
            cfg.risk.capital, cfg.risk.max_concurrent_positions
        ),
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
                + (f",risk_x{risk_multiplier:.2f}" if risk_multiplier != 1.0 else "")
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
    if outcome.gross_pnl_inr is not None:
        fields["gross_pnl_inr"] = outcome.gross_pnl_inr
    if outcome.costs_inr is not None:
        fields["costs_inr"] = outcome.costs_inr
    if outcome.days_held is not None:
        fields["days_held"] = outcome.days_held
    if outcome.notes:
        # Append rather than overwrite — multiple transitions can write notes
        fields["notes"] = outcome.notes
    # entry_price / entry_date are written once, at insert time — a trade is
    # already ENTERED when it is created, so no transition sets them.
    store.update_paper_trade(trade_id, **fields)
