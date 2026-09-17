"""Morning scan — runs at 9:30 AM IST.

Refreshes the NIFTY 500 universe (weekly), updates the price cache, then
scans every symbol through:

    1. Quality floor    (hard pass/fail)
    2. Stage filter     (only Stage 2 advances)
    3. Pattern detection
    4. Proximity check  (within `near_breakout_pct` of breakout level)
    5. Composite scoring

Symbols scoring at least 50 with a pattern and near-breakout proximity land
on the setup_watchlist. The pre-close scan re-checks these for actual
breakout confirmation (close > level AND volume >= 1.5x).

Run with:  python -m breakout.jobs.morning_scan
"""

from __future__ import annotations

import logging
from datetime import date

from breakout.analysis.indicators import add_standard_indicators
from breakout.analysis.patterns import detect_all
from breakout.analysis.rs import period_return, rs_percentile_ranks, rs_points
from breakout.analysis.session import drop_partial_bar
from breakout.analysis.stage import Stage, classify_stage
from breakout.analysis.tightness import tightness_score
from breakout.config import Config, ensure_runtime_dirs, load_config
from breakout.data.fetcher import (
    RateLimitError,
    fetch_earnings_dates,
    fetch_yf_index,
    make_fetcher,
)
from breakout.data.store import Store
from breakout.data.universe import refresh_universe_if_stale
from breakout.data.validate import is_continuous
from breakout.filters.earnings import in_earnings_blackout
from breakout.filters.gate_audit import audit_gates, render_audit
from breakout.filters.mood import assess_market, classify_sector, fetch_sector_trends
from breakout.filters.quality import check_quality
from breakout.filters.regime import assess_regime, market_breadth, pct_above_sma
from breakout.logging_setup import setup_logging
from breakout.scoring import ScoringFeatures, composite_score, scoring_params
from breakout.trading_calendar import last_completed_session, require_trading_day


logger = logging.getLogger(__name__)


# Watchlist threshold per the spec: setups in [50, min_score_to_alert) are
# candidates worth tracking even though they wouldn't alert by themselves.
_WATCHLIST_MIN_SCORE = 50.0
# We fetch a bit more than 252 trading days so 52w-high and 30-week SMA both
# have a comfortable buffer.
_PRICE_HISTORY_DAYS = 300
# Relative-strength lookback (≈ one quarter) and the sector-trend proxy band.
_RS_LOOKBACK = 63
_SECTOR_TREND_BAND = 0.02   # median member return beyond ±2% → up/down, else flat


def _sector_trends(universe: list[dict], returns: dict[str, float]) -> dict[str, str]:
    """Proxy each sector's trend from the median 63-day return of its members.

    A real implementation would use the live NIFTY sector indices; until that
    fetch is wired, the universe's own members are a serviceable stand-in.
    """
    import statistics
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for meta in universe:
        sector = classify_sector(meta.get("industry"))
        r = returns.get(meta["symbol"])
        if sector and r is not None:
            buckets[sector].append(r)
    trends: dict[str, str] = {}
    for sector, rs in buckets.items():
        med = statistics.median(rs) if rs else 0.0
        trends[sector] = "up" if med > _SECTOR_TREND_BAND else ("down" if med < -_SECTOR_TREND_BAND else "flat")
    return trends


def _earnings_dates(symbol: str, seen: dict | None = None) -> list:
    """Per-symbol earnings calendar (best-effort via yfinance). Returns [] when
    the source has no data, which the blackout gate treats as 'not in blackout'.

    NOTE: yfinance has ~no earnings coverage for NSE names (verified live —
    returns [] for RELIANCE/TCS/INFY), so the blackout gate is effectively
    inactive today. Swap in an Indian source (NSE announcements / Screener) to
    actually enable it.

    `seen` accumulates {checked, with_dates} so the gate audit can report that
    inertness from observation rather than from this comment."""
    dates = fetch_earnings_dates(symbol)
    if seen is not None:
        seen["checked"] += 1
        seen["with_dates"] += 1 if dates else 0
    return dates


def main() -> int:
    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    logger.info("=" * 50)
    logger.info("morning_scan starting")
    if not require_trading_day(date.today()):
        return 0

    with Store(cfg.paths.workbook) as store:
        run_id = store.start_run("morning_scan")
        try:
            n = _run(store, cfg)
            store.finish_run(run_id, "SUCCESS", alerts_generated=n)
            logger.info(f"morning_scan complete: {n} symbols on setup watchlist")
            return n
        except Exception as e:
            logger.exception("morning_scan failed")
            store.finish_run(run_id, "FAILED", error_message=str(e))
            raise


def _run(store: Store, cfg: Config) -> int:
    # 1. Refresh universe (weekly)
    count, refreshed = refresh_universe_if_stale(store)
    if not count:
        logger.error("universe is empty — aborting scan")
        return 0
    logger.info(f"universe: {count} symbols (refreshed={refreshed})")

    # Market mood (India VIX) — advisory: log the regime, warn if risk-off.
    mood = assess_market(fetch_yf_index("^INDIAVIX", days=60))
    vix_txt = f"{mood['vix']:.1f}" if mood["vix"] is not None else "n/a"
    logger.info(f"market mood: VIX={vix_txt} {mood['mood']} tradeable={mood['tradeable']}")
    if not mood["tradeable"]:
        logger.warning("India VIX elevated (risk-off) — treat today's breakouts with caution")

    universe = store.read_universe()
    symbols = [u["symbol"] for u in universe]

    # 2. Update price cache
    fetcher = make_fetcher(cfg)
    logger.info(f"fetching prices for {len(symbols)} symbols via {fetcher.__class__.__name__}")
    stale = [
        sym for sym in symbols
        if (latest := store.latest_price_date(sym)) is None
        or (date.today() - latest).days >= 1
    ]
    current = len(symbols) - len(stale)
    try:
        # Batched where the backend supports it (yfinance fetches ~50 tickers
        # per request), sequential otherwise.
        prices = fetcher.fetch_history_batch(stale, days=_PRICE_HISTORY_DAYS)
    except RateLimitError as e:
        # The backend has cut us off. Analysing a half-updated cache would
        # produce breakout signals from stale prices — worse than no scan.
        logger.error(f"aborting scan: {e}")
        return 0
    for sym, df in prices.items():
        store.upsert_prices(sym, df)
    fetched = len(prices)
    failed = len(stale) - fetched
    logger.info(f"prices: fetched={fetched}, failed={failed}, already-current={current}")

    # Staleness was decided *before* the fetch, so a symbol whose fetch then
    # failed still sits in the cache carrying old bars — and would be scanned
    # against them, producing a breakout signal from prices that are days old.
    # Re-check after the fetch, against the last session that actually closed
    # (not "today": at 9:30 today's bar is still being written and is
    # deliberately excluded — see analysis/session.py).
    required_bar = last_completed_session()
    fresh: set[str] = set()
    stale_symbols: list[str] = []
    for sym in symbols:
        latest = store.latest_price_date(sym)
        if latest is not None and latest >= required_bar:
            fresh.add(sym)
        else:
            stale_symbols.append(sym)
    if stale_symbols:
        logger.warning(
            f"{len(stale_symbols)} symbol(s) excluded — no bar for "
            f"{required_bar}, so any signal would come from old prices: "
            f"{', '.join(sorted(stale_symbols)[:10])}"
            f"{' ...' if len(stale_symbols) > 10 else ''}"
        )

    # 2b. Cross-sectional pre-pass: 63-day returns → relative-strength ranks,
    #     and a sector-trend proxy from those returns.
    # A series with an unadjusted split is excluded here, not just later: its
    # 63-day return would read as ~-50%, which drags the percentile of every
    # *other* symbol in the cross-section. One bad series would mis-score the
    # whole universe.
    # The same pass also counts market breadth (% of the universe above its own
    # 50DMA) — the regime gate's participation vote. It is free here: every
    # symbol's history is already in hand.
    returns: dict[str, float] = {}
    discontinuous: set[str] = set()
    breadth_above = breadth_total = 0
    for meta in universe:
        # Stale symbols are excluded from the cross-section too, not just from
        # the scan: a symbol frozen at last week's price would show a stale
        # 63-day return and distort every *other* symbol's RS percentile.
        if meta["symbol"] not in fresh:
            continue
        dfx = drop_partial_bar(
            store.read_prices(meta["symbol"], lookback_days=_PRICE_HISTORY_DAYS)
        )
        if len(dfx) and not is_continuous(dfx, meta["symbol"]):
            discontinuous.add(meta["symbol"])
            continue
        if len(dfx):
            above = pct_above_sma(dfx["close"], cfg.regime.breadth_sma)
            if above is not None:
                breadth_total += 1
                breadth_above += int(above)
        r = period_return(dfx, _RS_LOOKBACK) if len(dfx) else float("nan")
        if r == r:  # not NaN
            returns[meta["symbol"]] = r
    rs_ranks = rs_percentile_ranks(returns)
    logger.info(f"relative strength: ranked {len(rs_ranks)} symbols")

    # Regime: NIFTY trend + breadth + VIX → a multiplier on risk per trade.
    # Assessed here (not at pre-close) because breadth needs the whole universe;
    # it rides on the watchlist row so the 3 PM scan can size with it.
    breadth_pct = market_breadth(breadth_above, breadth_total)
    regime = assess_regime(
        fetch_yf_index(cfg.regime.nifty_symbol, days=_PRICE_HISTORY_DAYS),
        breadth_pct,
        mood["mood"],
        cfg.regime,
    )
    logger.info(f"regime: {regime.summary()} [{', '.join(regime.reasons)}]")
    if regime.risk_multiplier < 1.0:
        logger.warning(
            f"risk-off regime — position sizes scaled to "
            f"{regime.risk_multiplier:.0%} of normal today"
        )
    if discontinuous:
        logger.warning(
            f"{len(discontinuous)} symbol(s) excluded for price discontinuity: "
            f"{', '.join(sorted(discontinuous)[:10])}"
        )

    # Sector trend: prefer the live NIFTY sector indices; fall back to the
    # member-return proxy for any index that didn't fetch.
    present_sectors = {classify_sector(m.get("industry")) for m in universe} - {None}
    proxy = _sector_trends(universe, returns)
    real = fetch_sector_trends(present_sectors)
    sector_trend_map = {**proxy, **real}
    logger.info(f"sector trends: {len(real)} from indices, {len(proxy) - len(real)} proxy fallback")

    # 3. Run analysis per symbol
    setup_rows: list[dict] = []
    skipped: dict[str, int] = {"stale": 0, "insufficient_data": 0,
                                "discontinuous": 0, "quality": 0, "stage": 0,
                                "no_pattern": 0, "far_from_breakout": 0, "low_score": 0}
    # Observations that feed the gate audit at the end of the run — which of
    # the quality gates actually rejected anything today.
    earnings_seen = {"checked": 0, "with_dates": 0}
    quality_checked = 0
    adv_rejected = 0
    for meta in universe:
        symbol = meta["symbol"]
        if symbol not in fresh:
            skipped["stale"] += 1
            continue
        if symbol in discontinuous:
            skipped["discontinuous"] += 1
            continue
        # Today's bar is ~15 minutes old at 9:30 and holds ~4% of the session's
        # volume. Left in, every candidate's volume ratio reads as ~0.04x and
        # drags its score down for no reason but the clock; projecting from 4%
        # would multiply the opening fifteen minutes by ~26. The morning scan
        # judges setups on completed history — the pre-close scan does the real
        # volume check on a bar that is nearly whole.
        df = drop_partial_bar(
            store.read_prices(symbol, lookback_days=_PRICE_HISTORY_DAYS)
        )
        if len(df) < 200:
            skipped["insufficient_data"] += 1
            continue

        df = add_standard_indicators(df)

        # Quality floor
        qual = check_quality(df, meta, cfg.thresholds)
        quality_checked += 1
        if any(r.startswith("adv_below_floor") for r in qual.reasons_failed):
            adv_rejected += 1
        if not qual.passed:
            skipped["quality"] += 1
            continue

        # Stage 2 only
        stage = classify_stage(
            df,
            sma_bars=cfg.stage_filter.sma_weeks * 5,
            slope_lookback_bars=cfg.stage_filter.slope_lookback_weeks * 5,
            high_lookback_bars=cfg.stage_filter.high_lookback_weeks * 5,
        )
        if stage != Stage.STAGE_2:
            skipped["stage"] += 1
            continue

        # Pattern detection
        matches = detect_all(df, cfg.patterns.enabled)
        if not matches:
            skipped["no_pattern"] += 1
            continue
        top = matches[0]

        # Near-breakout proximity
        close = float(df["close"].iloc[-1])
        if top.breakout_level <= 0:
            skipped["no_pattern"] += 1
            continue
        distance_pct = (top.breakout_level - close) / top.breakout_level
        if distance_pct > cfg.thresholds.near_breakout_pct / 100.0:
            skipped["far_from_breakout"] += 1
            continue

        # Composite score — now with the full Phase 2 signal set.
        vol_ratio = df["volume_ratio_20"].iloc[-1]
        sector = classify_sector(meta.get("industry"))
        rs_percentile = rs_ranks.get(symbol, 0.0)
        tightness = tightness_score(df)
        sector_trend = sector_trend_map.get(sector, "flat")
        blackout = in_earnings_blackout(
            date.today(), _earnings_dates(symbol, earnings_seen)
        )
        features = ScoringFeatures(
            quality_pass=True,
            stage=Stage.STAGE_2,
            earnings_blackout=blackout,
            pattern_match=top,
            volume_ratio=float(vol_ratio) if vol_ratio == vol_ratio else 1.0,  # NaN-safe
            rs_score=rs_points(rs_percentile),
            tightness_score=tightness,
            sector_trend=sector_trend,
        )
        # No extension_pct here on purpose: price is still below the level, so
        # there is no entry to have chased. The penalty applies at confirmation.
        score = composite_score(features, **scoring_params(cfg.thresholds))
        if score < _WATCHLIST_MIN_SCORE:
            skipped["low_score"] += 1
            continue

        # Every feature is stored as its own column, not just folded into the
        # score: the pre-close scan reads them back onto the alert, and
        # `alert_features` needs them to answer which signals actually predict
        # an outcome. A score alone cannot be decomposed after the fact.
        setup_rows.append(
            {
                "symbol": symbol,
                "pattern": top.pattern_name,
                "breakout_level": top.breakout_level,
                "score": score,
                "detected_date": date.today().isoformat(),
                "base_height": top.base_height,
                "pattern_confidence": top.confidence,
                "stage": Stage.STAGE_2.value,
                "rs_percentile": rs_percentile,
                "rs_points": features.rs_score,
                "tightness": tightness,
                "volume_ratio": features.volume_ratio,
                "sector": sector,
                "sector_trend": sector_trend,
                "distance_pct": distance_pct * 100.0,
                "adv_cr": qual.adv_cr,
                "earnings_blackout": blackout,
                "vix": mood["vix"],
                "market_mood": mood["mood"],
                "regime": regime.label,
                "regime_score": regime.score,
                "breadth_pct": regime.breadth_pct,
                "nifty_trend": regime.nifty_trend,
                "risk_multiplier": regime.risk_multiplier,
                "notes": _format_notes(top.notes, qual.adv_cr, distance_pct),
            }
        )

    # 4. Persist watchlist
    store.replace_setup_watchlist(setup_rows)

    # 5. Prune stale pullback entries (Phase 2 will populate them)
    pruned = store.prune_pullback_watchlist(cfg.thresholds.pullback_window_days)
    if pruned:
        logger.info(f"pruned {pruned} stale pullback entries")

    logger.info(f"skip summary: {skipped}")

    # Which gates actually did anything today. Logged every run so a filter that
    # quietly stops working surfaces the next morning, not at the next review.
    logger.info(
        render_audit(
            audit_gates(
                universe,
                cfg.thresholds,
                adv_rejected=adv_rejected,
                symbols_checked=quality_checked,
                earnings_symbols_checked=earnings_seen["checked"],
                earnings_symbols_with_dates=earnings_seen["with_dates"],
            )
        )
    )
    return len(setup_rows)


def _format_notes(pattern_notes: dict, adv_cr: float, distance_pct: float) -> str:
    """Compact one-line summary for the watchlist row."""
    parts = [f"adv={adv_cr:.1f}cr", f"dist={distance_pct*100:.2f}%"]
    for k, v in pattern_notes.items():
        if isinstance(v, float):
            parts.append(f"{k}={v:.3f}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


if __name__ == "__main__":
    main()
