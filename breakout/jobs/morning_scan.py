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
from breakout.filters.earnings import in_earnings_blackout
from breakout.filters.mood import assess_market, classify_sector, fetch_sector_trends
from breakout.filters.quality import check_quality
from breakout.logging_setup import setup_logging
from breakout.scoring import ScoringFeatures, composite_score
from breakout.trading_calendar import require_trading_day


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


def _earnings_dates(symbol: str) -> list:
    """Per-symbol earnings calendar (best-effort via yfinance). Returns [] when
    the source has no data, which the blackout gate treats as 'not in blackout'.

    NOTE: yfinance has ~no earnings coverage for NSE names (verified live —
    returns [] for RELIANCE/TCS/INFY), so the blackout gate is effectively
    inactive today. Swap in an Indian source (NSE announcements / Screener) to
    actually enable it."""
    return fetch_earnings_dates(symbol)


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

    # 2b. Cross-sectional pre-pass: 63-day returns → relative-strength ranks,
    #     and a sector-trend proxy from those returns.
    returns: dict[str, float] = {}
    for meta in universe:
        dfx = store.read_prices(meta["symbol"], lookback_days=_PRICE_HISTORY_DAYS)
        r = period_return(dfx, _RS_LOOKBACK) if len(dfx) else float("nan")
        if r == r:  # not NaN
            returns[meta["symbol"]] = r
    rs_ranks = rs_percentile_ranks(returns)
    logger.info(f"relative strength: ranked {len(rs_ranks)} symbols")

    # Sector trend: prefer the live NIFTY sector indices; fall back to the
    # member-return proxy for any index that didn't fetch.
    present_sectors = {classify_sector(m.get("industry")) for m in universe} - {None}
    proxy = _sector_trends(universe, returns)
    real = fetch_sector_trends(present_sectors)
    sector_trend_map = {**proxy, **real}
    logger.info(f"sector trends: {len(real)} from indices, {len(proxy) - len(real)} proxy fallback")

    # 3. Run analysis per symbol
    setup_rows: list[dict] = []
    skipped: dict[str, int] = {"insufficient_data": 0, "quality": 0, "stage": 0,
                                "no_pattern": 0, "far_from_breakout": 0, "low_score": 0}
    for meta in universe:
        symbol = meta["symbol"]
        df = store.read_prices(symbol, lookback_days=_PRICE_HISTORY_DAYS)
        if len(df) < 200:
            skipped["insufficient_data"] += 1
            continue

        df = add_standard_indicators(df)

        # Quality floor
        qual = check_quality(df, meta, cfg.thresholds)
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
        features = ScoringFeatures(
            quality_pass=True,
            stage=Stage.STAGE_2,
            earnings_blackout=in_earnings_blackout(date.today(), _earnings_dates(symbol)),
            pattern_match=top,
            volume_ratio=float(vol_ratio) if vol_ratio == vol_ratio else 1.0,  # NaN-safe
            rs_score=rs_points(rs_ranks.get(symbol, 0.0)),
            tightness_score=tightness_score(df),
            sector_trend=sector_trend_map.get(sector, "flat"),
        )
        score = composite_score(features)
        if score < _WATCHLIST_MIN_SCORE:
            skipped["low_score"] += 1
            continue

        setup_rows.append(
            {
                "symbol": symbol,
                "pattern": top.pattern_name,
                "breakout_level": top.breakout_level,
                "score": score,
                "detected_date": date.today().isoformat(),
                "base_height": top.base_height,
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
