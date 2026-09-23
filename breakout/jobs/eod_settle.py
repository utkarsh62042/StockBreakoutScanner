"""End-of-day settle — runs at 4:00 PM IST.

For every open paper trade, fetches today's OHLC and walks it through the
state machine:

    ENTERED        -> STOPPED_OUT | TARGET_HIT | TARGET_1_HIT | TIME_EXIT
    TARGET_1_HIT   -> STOPPED_OUT (stop now at breakeven) | TARGET_HIT | TIME_EXIT

This is the audit log that lets us measure the scanner's edge. After 4–6
weeks of running, the paper_trades table answers: win rate by pattern, by
score band, by sector; average R-multiple; profit factor.

Run with:  python -m breakout.jobs.eod_settle
"""

from __future__ import annotations

import logging

from breakout.analysis.session import today_ist
from breakout.config import Config, ensure_runtime_dirs, load_config
from breakout.data.fetcher import FetchError, RateLimitError, make_fetcher
from breakout.data.store import Store
from breakout.logging_setup import setup_logging
from breakout.paper.tracker import (
    OPEN_STATES,
    apply_outcome,
    compute_daily_progress,
    compute_excursions,
    settle_one_trade,
)
from breakout.trading_calendar import require_trading_day


logger = logging.getLogger(__name__)


def main() -> int:
    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    logger.info("=" * 50)
    logger.info("eod_settle starting")
    if not require_trading_day(today_ist()):
        return 0

    with Store(cfg.paths.workbook) as store:
        run_id = store.start_run("eod_settle")
        try:
            n = _run(store, cfg)
            store.finish_run(run_id, "SUCCESS", alerts_generated=n)
            logger.info(f"eod_settle complete: {n} trades transitioned")
            return n
        except Exception as e:
            logger.exception("eod_settle failed")
            store.finish_run(run_id, "FAILED", error_message=str(e))
            raise


def _run(store: Store, cfg: Config) -> int:
    today = today_ist()
    open_trades = store.read_paper_trades_by_state(*OPEN_STATES)
    if not open_trades:
        logger.info("no open paper trades")
        return 0
    logger.info(f"settling {len(open_trades)} open trades")

    fetcher = make_fetcher(cfg)
    transitions = 0
    symbols_seen: set[str] = set()

    for trade in open_trades:
        symbol = trade["symbol"]

        # Fetch fresh end-of-day data once per symbol
        if symbol not in symbols_seen:
            try:
                df = fetcher.fetch_history(symbol, days=10)
                store.upsert_prices(symbol, df)
            except RateLimitError as e:
                logger.error(f"stopping settle pass: {e}")
                break
            except FetchError as e:
                logger.warning(f"skip {symbol}: {e}")
                continue
            symbols_seen.add(symbol)

        # Enough history to rebuild the whole day-by-day progress string for a
        # position held to its time-exit limit.
        prices = store.read_prices(
            symbol, lookback_days=cfg.paper_trading.hold_max_days + 10
        )
        if prices.empty:
            logger.warning(f"skip {symbol}: no cached prices")
            continue
        latest_row = prices.iloc[-1]
        # If the latest bar isn't from today, the market is closed (weekend
        # or holiday). Skip settle — there's no new info to act on.
        latest_date = prices.index[-1].date() if hasattr(prices.index[-1], "date") else None
        if latest_date != today:
            logger.debug(
                f"{symbol}: latest bar is {latest_date}, not today ({today}) — skipping"
            )
            continue

        today_ohlc = {
            "open": float(latest_row["open"]),
            "high": float(latest_row["high"]),
            "low": float(latest_row["low"]),
            "close": float(latest_row["close"]),
        }
        outcome = settle_one_trade(trade, today_ohlc, today, cfg)
        if outcome is not None:
            apply_outcome(store, trade["id"], outcome)
            transitions += 1

        _update_daily_progress(store, trade, prices, today)
        if outcome is None:
            continue

        logger.info(
            f"{symbol} #{trade['id']}: {trade['state']} -> {outcome.new_state} "
            f"({outcome.notes})"
        )

    failed = _flag_failed_breakouts(store, fetcher, today, symbols_seen)
    if failed:
        logger.info(f"{failed} failed breakout(s) flagged (EXIT_SIGNAL)")

    return transitions


def _update_daily_progress(store, trade: dict, prices, today: date) -> None:
    """Refresh the derived per-day columns for one trade.

    `days_in_trade` / `daily_moves` show how the position has behaved;
    `max_favorable` / `max_adverse` record the best and worst it ever got to in
    R, which is what says whether the target is too near or the stop too tight.
    All are recomputed from cached bars, so a re-run is idempotent.

    A trade is ENTERED from the moment it is created, so entry_date and
    entry_price are always already on the row.
    """
    fields: dict = {}
    label, moves = compute_daily_progress(
        trade.get("entry_date"), trade.get("entry_price"), prices, today
    )
    if label is not None:
        fields.update(days_in_trade=label, daily_moves=moves)

    favorable, adverse = compute_excursions(
        trade.get("entry_price"), trade.get("stop_loss"),
        prices, trade.get("entry_date"), today,
    )
    if favorable is not None:
        fields.update(max_favorable=favorable, max_adverse=adverse)

    if fields:
        store.update_paper_trade(trade["id"], **fields)


# A breakout that closes back below its level within this many days is "failed"
# — the classic false-breakout that should be exited, not held.
_FAILED_BREAKOUT_WINDOW_DAYS = 3


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _is_failed_breakout(
    breakout_date,
    breakout_level: float | None,
    today: date,
    today_close: float | None,
    window_days: int = _FAILED_BREAKOUT_WINDOW_DAYS,
) -> bool:
    """True if, within `window_days` of the breakout, price has closed back
    below the breakout level."""
    if breakout_level is None or today_close is None or not breakout_date:
        return False
    try:
        bd = date.fromisoformat(str(breakout_date)[:10])
    except ValueError:
        return False
    days = (today - bd).days
    return 0 <= days <= window_days and today_close < breakout_level


def _today_close(store, fetcher, symbol: str, today: date, symbols_seen: set) -> float | None:
    """Today's close for `symbol`, fetching once if not already cached today."""
    if symbol not in symbols_seen:
        try:
            store.upsert_prices(symbol, fetcher.fetch_history(symbol, days=10))
            symbols_seen.add(symbol)
        except FetchError as e:
            logger.warning(f"skip {symbol}: {e}")
            return None
    prices = store.read_prices(symbol, lookback_days=3)
    if prices.empty:
        return None
    if prices.index[-1].date() != today:
        return None
    return float(prices["close"].iloc[-1])


def _flag_failed_breakouts(store, fetcher, today: date, symbols_seen: set) -> int:
    """Scan recent breakouts (the pullback watchlist) for ones that have
    reversed below their level within the window; record each and drop it."""
    count = 0
    for row in store.read_pullback_watchlist():
        symbol = row["symbol"]
        level = _num(row.get("breakout_level"))
        bdate = row.get("breakout_date")
        close = _today_close(store, fetcher, symbol, today, symbols_seen)
        if not _is_failed_breakout(bdate, level, today, close):
            continue
        store.insert_failed_breakout(
            {
                "symbol": symbol,
                "breakout_date": bdate,
                "failure_date": today.isoformat(),
                "pattern": None,
                "original_score": row.get("original_score"),
            }
        )
        store.remove_pullback(symbol)
        logger.info(
            f"EXIT_SIGNAL {symbol}: closed {close:.2f} below breakout {level:.2f} "
            f"within {_FAILED_BREAKOUT_WINDOW_DAYS}d"
        )
        count += 1
    return count


if __name__ == "__main__":
    main()
