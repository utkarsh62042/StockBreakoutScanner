"""Pre-close scan — runs at 3:20 PM IST.

Re-fetches the latest day's data for every symbol on the setup_watchlist
and checks for actual breakout confirmation at 3:20 PM:

    close > breakout_level  AND  today_volume >= 1.5 × 20-day average

Confirmed breakouts:
  - emit an `ALERT: BREAKOUT` row through every active output channel
  - open a paper trade in ENTERED state at the price at 3:25 PM IST (5 minutes
    after scan for your manual execution), matching real execution: the trader
    buys in the 3:20–3:25 PM window on the confirmation day. Entry, stop,
    targets and position size are all derived from the confirmed price, so the
    quoted R:R is obtainable.
  - add to the pullback watchlist so future retests can be flagged

Run with:  python -m breakout.jobs.preclose_scan
"""

from __future__ import annotations

import logging

from breakout.analysis.indicators import atr
from breakout.analysis.patterns import PatternMatch
from breakout.analysis.session import (
    is_partial_bar,
    now_ist,
    project_full_day_volume,
    today_ist,
)
from breakout.analysis.stage import Stage
from breakout.config import Config, ensure_runtime_dirs, load_config
from datetime import timedelta

from breakout.data.fetcher import FetchError, RateLimitError, make_fetcher
from breakout.data.store import Store
from breakout.data.universe import refresh_universe_if_stale
from breakout.data.validate import is_continuous
from breakout.trading_calendar import active_holidays, is_trading_day
from breakout.logging_setup import setup_logging
from breakout.output.alerts import (
    Alert,
    build_channels,
    dispatch_alerts,
    render_alerts_table,
)
from breakout.filters.concentration import (
    Candidate,
    log_rejections,
    select_within_limits,
)
from breakout.filters.portfolio import should_scale_position_for_market_stress
from breakout.paper.tracker import (
    OPEN_STATES,
    compute_stop,
    compute_targets,
    insert_alert,
    max_position_value,
    position_size,
)
from breakout.scoring import ScoringFeatures, composite_score, scoring_params
from breakout.trading_calendar import require_trading_day


logger = logging.getLogger(__name__)


def main() -> int:
    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    logger.info("=" * 50)
    logger.info("preclose_scan starting")
    if not require_trading_day(today_ist()):
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
    # Idempotency check: avoid duplicate runs on the same day
    today = today_ist().date()
    recent_runs = store.read_run_log() or []

    # Check if preclose_scan already succeeded today
    for run in recent_runs[-5:]:  # Check last 5 runs
        if (run.get("job_name") == "preclose_scan" and
            run.get("status") == "SUCCESS" and
            run.get("started_at", "").startswith(str(today))):
            logger.warning(
                f"preclose_scan already completed successfully today ({today}). "
                f"Run ID #{run.get('id')}. Skipping duplicate run."
            )
            return 0

    # Refresh universe daily to catch newly indexed symbols
    count, refreshed = refresh_universe_if_stale(store, refresh_days=1)
    if refreshed:
        logger.info(f"universe refreshed: {count} symbols")
    else:
        logger.debug(f"universe cache current: {count} symbols")

    setup = store.read_setup_watchlist()
    if not setup:
        logger.info("setup_watchlist is empty — nothing to confirm")
        return 0

    fetcher = make_fetcher(cfg)
    alerts: list[Alert] = []
    confirmed_candidates: list[Candidate] = []

    # The morning scan assessed the regime (breadth needs the whole universe,
    # which this scan doesn't read) and wrote it onto every watchlist row. It is
    # a market-wide fact for the day, so reading it back is both cheap and
    # correct; rows written before the regime gate existed size at 1.0.
    risk_mult = _risk_multiplier(setup, cfg)

    # Additional market stress scaling if enabled: reduces position size in
    # high-VIX or narrow-breadth environments (FII selling, regime shifts)
    if cfg.risk.scale_positions_in_market_stress:
        # Read the regime info from the setup watchlist rows
        vix = _vix_from_setup(setup)
        breadth = _breadth_from_setup(setup)
        regime_label = _regime_label_from_setup(setup)
        stress_scale = should_scale_position_for_market_stress(vix, breadth, regime_label)
        if stress_scale < 1.0:
            logger.warning(
                f"market stress scaling active: position sizes scaled to "
                f"{stress_scale:.0%} (VIX={vix}, breadth={breadth}%)"
            )
            risk_mult *= stress_scale

    for row in setup:
        symbol = row["symbol"]
        try:
            # Short window — we just need today's close + 20 days of volume history
            df = fetcher.fetch_history(symbol, days=60)
            store.upsert_prices(symbol, df)
        except RateLimitError as e:
            logger.error(f"stopping confirmation pass: {e}")
            break
        except FetchError as e:
            logger.warning(f"skip {symbol}: {e}")
            continue
        df = store.read_prices(symbol, lookback_days=60)
        if len(df) < 21:
            logger.warning(f"skip {symbol}: only {len(df)} bars cached")
            continue
        # A fetch can succeed and still hand back nothing for today. This scan
        # is asking "did it break out *today*", so without today's bar there is
        # no question to answer — confirming against yesterday's close would
        # open a position on a breakout that may already be over.
        if not _has_todays_bar(df):
            logger.warning(f"skip {symbol}: no bar for today — cannot confirm")
            continue
        # Re-checked here, not just inherited from the morning: an action can go
        # ex-date today, and this is the scan that commits real money.
        if not is_continuous(df, symbol):
            continue

        close = float(df["close"].iloc[-1])
        vol_ratio = _volume_ratio(df)

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

        # Rescore properly: the morning stored every feature as a column, so
        # the whole composite can be recomputed with today's volume rather than
        # patched arithmetically.
        score = _rescore(row, vol_ratio, cfg, entry_price=close, bar=df.iloc[-1])
        if score < cfg.thresholds.min_score_to_alert:
            logger.debug(f"{symbol}: confirmed but score {score:.1f} < {cfg.thresholds.min_score_to_alert}")
            continue

        # Confirmed and good enough — but whether it is actually *taken* depends
        # on capacity, which can only be decided once every candidate is known.
        confirmed_candidates.append(
            Candidate(
                symbol=symbol,
                score=score,
                payload={
                    "row": row,
                    "close": close,
                    "vol_ratio": vol_ratio,
                    "breakout_level": breakout_level,
                    "atr_14": float(atr(df, 14).iloc[-1]),
                    "bar": df.iloc[-1],
                },
            )
        )

    # ── Concentration limits ────────────────────────────────────────────────
    # Second pass: the day's open slots go to the highest-scoring confirmations,
    # not to whichever symbol happened to sort first in the watchlist.
    accepted, rejected = select_within_limits(
        confirmed_candidates,
        store.read_paper_trades_by_state(*OPEN_STATES),
        {m["symbol"]: m for m in store.read_universe()},
        cfg.risk.max_concurrent_positions,
        cfg.risk.max_positions_per_sector,
    )
    log_rejections(rejected)

    today = today_ist()
    is_friday = today.weekday() == 4  # 0=Mon, 4=Fri

    # If Friday, check if it's a holiday-shortened week (Monday is not a trading day)
    is_multi_day_gap = False
    if is_friday:
        monday = today + timedelta(days=3)  # Friday + 3 days = Monday
        is_multi_day_gap = not is_trading_day(monday, active_holidays())

    for cand in accepted:
        p = cand.payload
        row, close, breakout_level = p["row"], p["close"], p["breakout_level"]
        atr_14, vol_ratio, score = p["atr_14"], p["vol_ratio"], cand.score
        symbol = cand.symbol

        # Gate Friday entries: weekend gap risk. Use 2.5x ATR (regular Friday) or 3.5x ATR (holiday-shortened week)
        normal_mult = cfg.paper_trading.atr_stop_multiplier
        if is_friday:
            atr_mult = 3.5 if is_multi_day_gap else 2.5
            gap_type = "3+ day gap (Monday holiday)" if is_multi_day_gap else "weekend gap"
            logger.warning(
                f"{symbol}: Friday entry with {gap_type} (stop: {atr_mult}x ATR={atr_14*atr_mult:.2f} vs "
                f"normal {normal_mult}x ATR={atr_14*normal_mult:.2f})"
            )
        else:
            atr_mult = normal_mult
        stop = compute_stop(breakout_level, atr_14, atr_mult)
        target_1, target_2 = compute_targets(
            close, stop, float(row.get("base_height") or 0), cfg.paper_trading.target_1_r_multiple
        )
        shares = position_size(
            cfg.risk.capital, cfg.risk.risk_per_trade_pct * risk_mult, close, stop,
            max_value=max_position_value(
                cfg.risk.capital, cfg.risk.max_concurrent_positions
            ),
        )

        # The real feature values, not the zeros this used to report.
        alerts.append(
            Alert(
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
                stage=str(row.get("stage") or "STAGE_2"),
                rs_rank=_num(row.get("rs_percentile")),
                tightness=_num(row.get("tightness")),
                sector=str(row.get("sector") or "unknown"),
                notes=row.get("notes", ""),
                alert_type="BREAKOUT",
            )
        )

        # Paper trade opened at this scan's price — same numbers as the alert
        trade_id = insert_alert(
            store,
            symbol=symbol,
            pattern=row["pattern"],
            alert_type="BREAKOUT",
            score=score,
            breakout_level=breakout_level,
            entry_price=close,
            atr=atr_14,
            base_height=float(row.get("base_height") or 0),
            cfg=cfg,
            risk_multiplier=risk_mult,
        )
        _record_features(
            store, trade_id, row, alert_type="BREAKOUT", score=score,
            entry_price=close, breakout_level=breakout_level,
            vol_ratio=vol_ratio, atr_14=atr_14, bar=p["bar"],
            risk_multiplier=risk_mult,
        )

        # Add to pullback watchlist for future retest alerts
        store.upsert_pullback_watchlist(
            [
                {
                    "symbol": symbol,
                    "breakout_date": today_ist().isoformat(),
                    "breakout_level": breakout_level,
                    "original_score": score,
                    "notes": f"pattern={row['pattern']}",
                }
            ]
        )

    # ── Pullback entries: a recent breakout retests its level and holds ──────
    # Prune old breakouts that have aged out of the retest window
    pruned = store.prune_pullback_watchlist(cfg.thresholds.pullback_window_days)
    if pruned > 0:
        logger.info(f"pruned {pruned} stale breakouts from pullback watchlist")

    for row in store.read_pullback_watchlist():
        symbol = row["symbol"]
        level = float(row["breakout_level"]) if row.get("breakout_level") else 0.0
        if level <= 0:
            continue

        # Skip if a pullback entry already fired for this exact breakout level
        existing_pullback = _has_existing_pullback_entry(store, symbol, level)
        if existing_pullback:
            logger.debug(f"skip {symbol} @ {level:.2f}: pullback entry already fired")
            store.remove_pullback(symbol)
            continue

        try:
            df = fetcher.fetch_history(symbol, days=60)
            store.upsert_prices(symbol, df)
        except RateLimitError as e:
            logger.error(f"stopping pullback pass: {e}")
            break
        except FetchError as e:
            logger.warning(f"skip pullback {symbol}: {e}")
            continue
        df = store.read_prices(symbol, lookback_days=60)
        if len(df) < 21 or not is_continuous(df, symbol):
            continue
        if not _has_todays_bar(df):
            logger.warning(f"skip pullback {symbol}: no bar for today")
            continue
        if not _is_pullback_entry(df, level):
            continue
        # A pullback entry consumes a slot exactly like a fresh breakout, so it
        # has to clear the same limits. Re-checked per symbol because the loop
        # above has just opened positions.
        if not _has_capacity(store, cfg, symbol):
            logger.warning(
                f"pullback {symbol}: not taken — at concentration limits"
            )
            continue

        close = float(df["close"].iloc[-1])
        vol_ratio = _volume_ratio(df)
        atr_14 = float(atr(df, 14).iloc[-1])
        # Friday pullback entries also get wider stop for weekend/multi-day gap protection
        if is_friday:
            atr_mult = 3.5 if is_multi_day_gap else 2.5
        else:
            atr_mult = cfg.paper_trading.atr_stop_multiplier
        stop = compute_stop(level, atr_14, atr_mult)
        target_1, target_2 = compute_targets(
            close, stop, close - level, cfg.paper_trading.target_1_r_multiple
        )
        shares = position_size(
            cfg.risk.capital, cfg.risk.risk_per_trade_pct * risk_mult, close, stop,
            max_value=max_position_value(
                cfg.risk.capital, cfg.risk.max_concurrent_positions
            ),
        )
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
        trade_id = insert_alert(
            store, symbol=symbol, pattern="pullback", alert_type="PULLBACK_ENTRY",
            score=pb_score, breakout_level=level, entry_price=close, atr=atr_14,
            base_height=max(0.0, close - level), cfg=cfg,
            risk_multiplier=risk_mult,
        )
        # A pullback entry fires off the watchlist, not the morning scan, so the
        # morning feature columns aren't available — only what today's bar shows.
        _record_features(
            store, trade_id, {"symbol": symbol, "pattern": "pullback"},
            alert_type="PULLBACK_ENTRY", score=pb_score, entry_price=close,
            breakout_level=level, vol_ratio=vol_ratio, atr_14=atr_14,
            bar=df.iloc[-1], risk_multiplier=risk_mult,
        )
        # Remove from watchlist to prevent re-triggering on subsequent days
        store.remove_pullback(symbol)

    # Dispatch
    render_alerts_table(alerts, title=f"Pre-close alerts {today_ist()}")
    channels = build_channels(cfg)
    dispatch_alerts(alerts, channels)

    return len(alerts)


def _has_todays_bar(df, at=None) -> bool:
    """Does the frame's last bar belong to the current IST trading day?

    Deliberately IST rather than the machine clock: a scheduler running in
    another timezone could otherwise call yesterday's bar "today" and confirm
    breakouts against it.
    """
    if df is None or len(df) == 0:
        return False
    last = df.index[-1]
    last_date = last.date() if hasattr(last, "date") else None
    return last_date == (at or now_ist()).date()


def _volume_ratio(df, at=None) -> float:
    """Today's volume vs the trailing 20-day average, both on a full-day basis.

    This scan runs at 3:00 PM, so today's bar holds only ~85% of the session's
    volume while the 20-day mean is made of complete days. Comparing them raw —
    which is what this did before — makes the 1.5x gate systematically harder to
    clear than configured: a genuine 1.5x day reads as ~1.27x and is rejected.

    The partial bar is therefore projected to a full-day estimate using the
    measured intraday profile (see `analysis.session`). After the close, or on a
    settled bar, the projection is a no-op.
    """
    vol_avg_20 = float(df["volume"].iloc[-21:-1].mean())
    if vol_avg_20 <= 0:
        return 0.0
    today_volume = float(df["volume"].iloc[-1])
    last = df.index[-1]
    if is_partial_bar(last.date() if hasattr(last, "date") else None, at):
        today_volume, _ = project_full_day_volume(today_volume, at)
    return today_volume / vol_avg_20


def _has_capacity(store, cfg: Config, symbol: str) -> bool:
    """Would opening `symbol` right now stay inside both concentration limits?"""
    accepted, _ = select_within_limits(
        [Candidate(symbol=symbol, score=0.0, payload={})],
        store.read_paper_trades_by_state(*OPEN_STATES),
        {m["symbol"]: m for m in store.read_universe()},
        cfg.risk.max_concurrent_positions,
        cfg.risk.max_positions_per_sector,
    )
    return bool(accepted)


def _risk_multiplier(setup: list[dict], cfg: Config) -> float:
    """The day's regime sizing multiplier, read off the watchlist.

    Every row carries the same value (it's one assessment per morning), so the
    first usable one wins. Defaults to 1.0 — full size — when the gate is off or
    the column is absent, because sizing down on a missing value would penalise
    trades for a data gap rather than for the tape.
    """
    if not cfg.regime.enabled:
        return 1.0
    for row in setup:
        m = _num(row.get("risk_multiplier"), default=0.0)
        if m > 0:
            if m < 1.0:
                logger.warning(
                    f"regime {row.get('regime') or 'risk_off'} — sizing today's "
                    f"entries at {m:.0%} of normal risk"
                )
            return m
    return 1.0


def _num(v, default: float = 0.0) -> float:
    """Watchlist cells come back as None or NaN when a column was never set."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default


def _rescore(
    row: dict,
    vol_ratio: float,
    cfg: Config | None = None,
    entry_price: float | None = None,
    bar=None,
) -> float:
    """Recompute the composite score with the confirmation day's volume.

    The morning persists each feature as its own column, so this is the real
    `composite_score` over the real inputs. It replaces an arithmetic patch that
    added the new volume term and subtracted an assumed-1.0 one — close, but it
    silently drifted from the scorer any time a weight changed.

    Falls back to the stored score if the row predates the feature columns.
    """
    if row.get("pattern_confidence") is None:
        return _num(row.get("score"))
    match = PatternMatch(
        detected=True,
        pattern_name=str(row.get("pattern") or ""),
        confidence=_num(row.get("pattern_confidence")),
        breakout_level=_num(row.get("breakout_level")),
        base_start_idx=0,
        base_end_idx=0,
        base_height=_num(row.get("base_height")),
        notes={},
    )
    params = scoring_params(cfg.thresholds) if cfg is not None else {}
    # How far above the level this entry would print — the chase penalty's
    # input. This is the scan that commits money, so it is the right place to
    # dock a score for paying up.
    level = _num(row.get("breakout_level"))
    extension_pct = (
        (entry_price - level) / level * 100.0
        if level > 0 and entry_price is not None
        else None
    )
    return composite_score(
        ScoringFeatures(
            quality_pass=True,
            stage=Stage.STAGE_2,
            # Re-checked every morning; a stock entering a blackout between the
            # two scans would have been dropped from the watchlist, not rescored.
            earnings_blackout=False,
            pattern_match=match,
            volume_ratio=vol_ratio,
            rs_score=_num(row.get("rs_points")),
            tightness_score=_num(row.get("tightness")),
            sector_trend=str(row.get("sector_trend") or "flat"),
            extension_pct=extension_pct,
            # Closing on the high is the direct evidence of buying into the
            # close; `close > level` alone can't tell that from a bar that gave
            # back everything it gained.
            close_in_range=close_in_range(bar) if bar is not None else None,
        ),
        **params,
    )


def close_in_range(bar) -> float | None:
    """Where the bar closed within its own range: 1.0 = at the high, 0.0 = low.

    A breakout closing on its high is a different animal from one that closed
    near its low but still above the level, and `close > level` cannot tell them
    apart. Logged as a feature now; scoring it is a decision for after the
    paper-trade sample says whether it matters here.
    """
    high, low, close = (_num(bar.get(k), float("nan")) for k in ("high", "low", "close"))
    span = high - low
    if span <= 0 or span != span:
        return None
    return (close - low) / span


def _record_features(
    store,
    trade_id: int,
    row: dict,
    *,
    alert_type: str,
    score: float,
    entry_price: float,
    breakout_level: float,
    vol_ratio: float,
    atr_14: float,
    bar,
    risk_multiplier: float = 1.0,
) -> None:
    """Persist the signal snapshot behind one alert, joined to its trade id."""
    store.insert_alert_features(
        {
            "trade_id": trade_id,
            "symbol": row.get("symbol"),
            "alert_date": today_ist().isoformat(),
            "alert_type": alert_type,
            "pattern": row.get("pattern"),
            "score": score,
            "pattern_confidence": row.get("pattern_confidence"),
            "stage": row.get("stage"),
            "rs_percentile": row.get("rs_percentile"),
            "rs_points": row.get("rs_points"),
            "tightness": row.get("tightness"),
            "volume_ratio": vol_ratio,
            "volume_ratio_morning": row.get("volume_ratio"),
            "sector": row.get("sector"),
            "sector_trend": row.get("sector_trend"),
            "distance_pct": row.get("distance_pct"),
            # How far above the level we actually paid — the chase penalty.
            "extension_pct": (
                (entry_price - breakout_level) / breakout_level * 100.0
                if breakout_level > 0 else None
            ),
            "close_in_range": close_in_range(bar),
            # ATR as a share of entry, so volatility is comparable across names.
            "atr_pct": atr_14 / entry_price * 100.0 if entry_price > 0 else None,
            "adv_cr": row.get("adv_cr"),
            "earnings_blackout": row.get("earnings_blackout"),
            "vix": row.get("vix"),
            "market_mood": row.get("market_mood"),
            # Regime is logged per alert precisely so its thresholds can be set
            # from realised outcomes later instead of the priors in config.
            "regime": row.get("regime"),
            "regime_score": row.get("regime_score"),
            "breadth_pct": row.get("breadth_pct"),
            "nifty_trend": row.get("nifty_trend"),
            "risk_multiplier": risk_multiplier,
            "breakout_level": breakout_level,
            "entry_price": entry_price,
            "base_height": row.get("base_height"),
        }
    )


# Today's low may dip to within this fraction ABOVE the breakout level and still
# count as a successful retest (support holding just above prior resistance).
_PULLBACK_TOUCH_PCT = 0.01


def _has_existing_pullback_entry(store: Store, symbol: str, level: float) -> bool:
    """Check if a PULLBACK_ENTRY alert already fired for this symbol at this level
    (regardless of whether it's still open or closed). Allows multiple pullback
    entries for the same symbol if they're at different breakout levels."""
    trades = store.read_paper_trades_by_state()  # All trades, any state
    for trade in trades:
        if (trade.get("symbol") == symbol and
            trade.get("alert_type") == "PULLBACK_ENTRY" and
            abs(float(trade.get("breakout_level") or 0) - level) < 0.01):  # Within 1 paisa
            return True
    return False


def _is_pullback_entry(df, level: float, touch_pct: float = _PULLBACK_TOUCH_PCT) -> bool:
    """Retest-and-hold: today's low dipped to the breakout level (within
    `touch_pct` above it) but price closed back above the level on a reversal
    candle with sufficient strength (must close in upper half of range or well
    above the level to confirm institutional support)."""
    o = float(df["open"].iloc[-1])
    c = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    high = float(df["high"].iloc[-1])

    touched = level <= low <= level * (1.0 + touch_pct)
    above = c > level
    body = abs(c - o)
    lower_wick = min(o, c) - low
    reversal = lower_wick > body or c > o

    # Additional strength requirements to avoid false positives on weak closes
    # Require either: (a) close in upper 50% of range, OR (b) close >= 1% above level
    daily_range = high - low
    close_position = (c - low) / daily_range if daily_range > 0 else 0
    close_above_level = c - level
    min_close_distance = level * 0.01  # 1% above level for confirmation

    # Either: close in upper half of range (>=50%), or close at least 1% above the level
    strong_close = close_position >= 0.5 or close_above_level >= min_close_distance

    return touched and above and reversal and strong_close


def _vix_from_setup(setup: list[dict]) -> float | None:
    """Extract India VIX level from first setup row (same for all rows)."""
    for row in setup:
        v = _num(row.get("vix"))
        if v > 0:
            return v
    return None


def _breadth_from_setup(setup: list[dict]) -> float | None:
    """Extract market breadth % from first setup row."""
    for row in setup:
        b = _num(row.get("breadth_pct"))
        if b is not None and b >= 0:
            return b
    return None


def _regime_label_from_setup(setup: list[dict]) -> str | None:
    """Extract regime label from first setup row."""
    for row in setup:
        r = str(row.get("regime") or "").strip()
        if r and r.lower() in ("risk_on", "neutral", "risk_off"):
            return r.lower()
    return None


if __name__ == "__main__":
    main()
