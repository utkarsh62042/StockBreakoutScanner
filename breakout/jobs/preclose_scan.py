"""Pre-close scan — runs at 3:00 PM IST.

Re-fetches the latest day's data for every symbol on the setup_watchlist
and checks for actual breakout confirmation:

    close > breakout_level  AND  today_volume >= 1.5 × 20-day average

Confirmed breakouts:
  - emit an `ALERT: BREAKOUT` row through every active output channel
  - log a paper trade in ALERTED state (entry happens at next day's open
    per the realistic-execution model)
  - add to the pullback watchlist so future retests can be flagged

Run with:  python -m breakout.jobs.preclose_scan
"""

from __future__ import annotations

import logging
from datetime import date

from breakout.analysis.indicators import atr
from breakout.analysis.patterns import PatternMatch
from breakout.config import Config, ensure_runtime_dirs, load_config
from breakout.data.fetcher import FetchError, make_fetcher
from breakout.data.store import Store
from breakout.logging_setup import setup_logging
from breakout.output.alerts import (
    Alert,
    build_channels,
    dispatch_alerts,
    render_alerts_table,
)
from breakout.paper.tracker import (
    compute_stop,
    compute_targets,
    insert_alert,
    position_size,
)
from breakout.trading_calendar import require_trading_day


logger = logging.getLogger(__name__)


def main() -> int:
    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    logger.info("=" * 50)
    logger.info("preclose_scan starting")
    if not require_trading_day(date.today()):
        return 0

    with Store(cfg.paths.workbook) as store:
        run_id = store.start_run("preclose_scan")
        try:
            n = _run(store, cfg)
            store.finish_run(run_id, "SUCCESS", alerts_generated=n)
            logger.info(f"preclose_scan complete: {n} alerts emitted")
            return n
        except Exception as e:
            logger.exception("preclose_scan failed")
            store.finish_run(run_id, "FAILED", error_message=str(e))
            raise


def _run(store: Store, cfg: Config) -> int:
    setup = store.read_setup_watchlist()
    if not setup:
        logger.info("setup_watchlist is empty — nothing to confirm")
        return 0

    fetcher = make_fetcher(cfg)
    alerts: list[Alert] = []

    for row in setup:
        symbol = row["symbol"]
        try:
            # Short window — we just need today's close + 20 days of volume history
            df = fetcher.fetch_history(symbol, days=60)
            store.upsert_prices(symbol, df)
        except FetchError as e:
            logger.warning(f"skip {symbol}: {e}")
            continue
        df = store.read_prices(symbol, lookback_days=60)
        if len(df) < 21:
            logger.warning(f"skip {symbol}: only {len(df)} bars cached")
            continue

        close = float(df["close"].iloc[-1])
        today_volume = float(df["volume"].iloc[-1])
        vol_avg_20 = float(df["volume"].iloc[-21:-1].mean())
        vol_ratio = today_volume / vol_avg_20 if vol_avg_20 > 0 else 0.0

        breakout_level = float(row["breakout_level"])
        confirmed = (
            close > breakout_level
            and vol_ratio >= cfg.thresholds.min_volume_ratio
        )

        if not confirmed:
            logger.debug(
                f"{symbol}: not confirmed (close={close:.2f} vs lvl={breakout_level:.2f}, vol_ratio={vol_ratio:.2f})"
            )
            continue

        # Recompute score with today's volume — could lift a 55 → 70.
        # For Phase 1 we trust the morning score and only check it against the
        # alert threshold. Phase 2 will rescore here with full features.
        score = float(row["score"]) + min(15.0, (vol_ratio / 1.5) * 15.0) - min(15.0, 1.0 * 15.0 / 1.5)
        # The delta above replaces the assumed-1.0 volume_ratio used in the
        # morning score with the actual ratio observed today. Approximate but
        # captures the volume-confirmation lift.
        if score < cfg.thresholds.min_score_to_alert:
            logger.debug(f"{symbol}: confirmed but score {score:.1f} < {cfg.thresholds.min_score_to_alert}")
            continue

        # Build alert
        atr_14 = float(atr(df, 14).iloc[-1])
        stop = compute_stop(breakout_level, atr_14, cfg.paper_trading.atr_stop_multiplier)
        target_1, target_2 = compute_targets(
            close, stop, float(row.get("base_height") or 0), cfg.paper_trading.target_1_r_multiple
        )
        shares = position_size(cfg.risk.capital, cfg.risk.risk_per_trade_pct, close, stop)

        alert = Alert(
            symbol=symbol,
            score=score,
            pattern=row["pattern"],
            breakout_level=breakout_level,
            entry_price=close,
            stop_loss=stop,
            target_1=target_1,
            target_2=target_2,
            position_size=shares,
            volume_ratio=vol_ratio,
            stage="STAGE_2",
            rs_rank=0.0,
            tightness=0.0,
            sector="flat",
            notes=row.get("notes", ""),
            alert_type="BREAKOUT",
        )
        alerts.append(alert)

        # Paper trade in ALERTED state
        insert_alert(
            store,
            symbol=symbol,
            pattern=row["pattern"],
            alert_type="BREAKOUT",
            score=score,
            breakout_level=breakout_level,
            atr=atr_14,
            base_height=float(row.get("base_height") or 0),
            cfg=cfg,
        )

        # Add to pullback watchlist for future retest alerts
        store.upsert_pullback_watchlist(
            [
                {
                    "symbol": symbol,
                    "breakout_date": date.today().isoformat(),
                    "breakout_level": breakout_level,
                    "original_score": score,
                    "notes": f"pattern={row['pattern']}",
                }
            ]
        )

    # ── Pullback entries: a recent breakout retests its level and holds ──────
    for row in store.read_pullback_watchlist():
        symbol = row["symbol"]
        level = float(row["breakout_level"]) if row.get("breakout_level") else 0.0
        if level <= 0:
            continue
        try:
            df = fetcher.fetch_history(symbol, days=60)
            store.upsert_prices(symbol, df)
        except FetchError as e:
            logger.warning(f"skip pullback {symbol}: {e}")
            continue
        df = store.read_prices(symbol, lookback_days=60)
        if len(df) < 21 or not _is_pullback_entry(df, level):
            continue

        close = float(df["close"].iloc[-1])
        today_volume = float(df["volume"].iloc[-1])
        vol_avg_20 = float(df["volume"].iloc[-21:-1].mean())
        vol_ratio = today_volume / vol_avg_20 if vol_avg_20 > 0 else 0.0
        atr_14 = float(atr(df, 14).iloc[-1])
        stop = compute_stop(level, atr_14, cfg.paper_trading.atr_stop_multiplier)
        target_1, target_2 = compute_targets(
            close, stop, close - level, cfg.paper_trading.target_1_r_multiple
        )
        shares = position_size(cfg.risk.capital, cfg.risk.risk_per_trade_pct, close, stop)
        pb_score = float(row.get("original_score") or 0.0)

        alerts.append(
            Alert(
                symbol=symbol, score=pb_score, pattern="pullback",
                breakout_level=level, entry_price=close, stop_loss=stop,
                target_1=target_1, target_2=target_2, position_size=shares,
                volume_ratio=vol_ratio, stage="STAGE_2", alert_type="PULLBACK_ENTRY",
                notes=str(row.get("notes") or ""),
            )
        )
        insert_alert(
            store, symbol=symbol, pattern="pullback", alert_type="PULLBACK_ENTRY",
            score=pb_score, breakout_level=level, atr=atr_14,
            base_height=max(0.0, close - level), cfg=cfg,
        )

    # Dispatch
    render_alerts_table(alerts, title=f"Pre-close alerts {date.today()}")
    channels = build_channels(cfg)
    dispatch_alerts(alerts, channels)

    return len(alerts)


# Today's low may dip to within this fraction ABOVE the breakout level and still
# count as a successful retest (support holding just above prior resistance).
_PULLBACK_TOUCH_PCT = 0.01


def _is_pullback_entry(df, level: float, touch_pct: float = _PULLBACK_TOUCH_PCT) -> bool:
    """Retest-and-hold: today's low dipped to the breakout level (within
    `touch_pct` above it) but price closed back above the level on a reversal
    candle (longer lower wick than body, or an up close)."""
    o = float(df["open"].iloc[-1])
    c = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    touched = level <= low <= level * (1.0 + touch_pct)
    above = c > level
    body = abs(c - o)
    lower_wick = min(o, c) - low
    reversal = lower_wick > body or c > o
    return touched and above and reversal


if __name__ == "__main__":
    main()
