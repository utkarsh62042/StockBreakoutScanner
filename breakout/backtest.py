"""Backtest harness — replay the scanner's logic over historical bars.

**These numbers are not an edge estimate. Do not size real capital on them.**
This is a regression check on the detector wiring: it answers "does the pipeline
still fire and settle trades the way it did last week", not "does this strategy
make money". Five reasons, none of them cheaply fixable — see `caveats()` for
the text the CLI prints, and `IMPROVEMENTS.md` #11 / #15.

The forward paper-trade log is the edge estimate. This is a smoke test.


Walks each symbol's cached history day-by-day: at each day a *signal function*
decides whether a breakout would have fired; if so a trade is simulated forward
using the exact same stop / target / time-exit rules as the live paper tracker.
Results aggregate through `output.digest` so backtest and paper stats read the
same way.

The signal function is injected (`run_backtest(..., signal_fn=...)`) so the core
is testable with a trivial signal; the CLI wires the real detector-based trigger
(`default_signal`), which mirrors the morning-scan gate (Stage 2 + pattern +
near-breakout proximity + volume + score threshold; RS/sector are omitted since
they need the whole universe as-of each day).

Run with:  python -m breakout.backtest --days 60
"""

from __future__ import annotations

import argparse
import logging
from typing import Callable

import pandas as pd

from breakout.analysis.indicators import atr
from breakout.paper.costs import settle_pnl
from breakout.paper.tracker import (
    TradeState,
    compute_stop,
    compute_targets,
    max_position_value,
    position_size,
)

logger = logging.getLogger(__name__)

# (breakout_level, base_height, pattern_name, score) or None
Signal = Callable[[pd.DataFrame], "tuple | None"]

_MIN_HISTORY = 200   # bars of warmup before any signal can fire


def _d(ts) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    breakout_level: float,
    atr_val: float,
    base_height: float,
    cfg,
) -> tuple[dict | None, int]:
    """Simulate one trade entered at `df.close[entry_idx]` — the bar on which
    the signal fired, filled at the 3 PM pre-close price, exactly as the live
    tracker does. Walk forward until stop / target_2 / time-exit. Returns
    (trade_dict, exit_idx). trade_dict is None (with exit_idx == entry_idx) if
    the setup has non-positive risk.

    Exit checks start the bar AFTER entry: the entry bar's high and low mostly
    printed before we were in the position, so testing them would credit fills
    that were never available.

    target_1 moves the stop to breakeven (matches the live tracker)."""
    n = len(df)
    if entry_idx >= n:
        return None, entry_idx
    entry = float(df["close"].iloc[entry_idx])
    stop = compute_stop(breakout_level, atr_val, cfg.paper_trading.atr_stop_multiplier)
    risk = entry - stop
    if risk <= 0:
        return None, entry_idx
    t1, t2 = compute_targets(entry, stop, base_height, cfg.paper_trading.target_1_r_multiple)
    shares = position_size(
        cfg.risk.capital, cfg.risk.risk_per_trade_pct, entry, stop,
        max_value=max_position_value(
            cfg.risk.capital, cfg.risk.max_concurrent_positions
        ),
    )

    last = min(entry_idx + cfg.paper_trading.hold_max_days, n - 1)
    if last <= entry_idx:
        return None, entry_idx  # no bar after entry — nothing to settle against
    cur_stop = stop
    t1_hit = False
    state = TradeState.TIME_EXIT
    exit_price = float(df["close"].iloc[last])
    exit_idx = last
    for j in range(entry_idx + 1, last + 1):
        op = float(df["open"].iloc[j])
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        cl = float(df["close"].iloc[j])
        if lo <= cur_stop:
            # Gap through the stop fills at the open, not at the stop — mirrors
            # `tracker.settle_one_trade`. Applies to the breakeven stop too.
            state, exit_price, exit_idx = TradeState.STOPPED_OUT, min(cur_stop, op), j
            break
        if hi >= t2:
            state, exit_price, exit_idx = TradeState.TARGET_HIT, t2, j
            break
        if not t1_hit and hi >= t1:
            t1_hit, cur_stop = True, entry   # first target → stop to breakeven
        if j == last:
            state, exit_price, exit_idx = TradeState.TIME_EXIT, cl, j

    gross, costs_inr, net, net_r = settle_pnl(entry, exit_price, shares, stop, cfg.costs)
    trade = {
        "entry_date": _d(df.index[entry_idx]), "entry_price": entry,
        "exit_date": _d(df.index[exit_idx]), "exit_price": exit_price,
        "state": state, "pnl_r": net_r,
        "pnl_inr": net, "gross_pnl_inr": gross, "costs_inr": costs_inr,
        "days_held": exit_idx - entry_idx,
        "shares": shares, "stop_loss": stop, "target_1": t1, "target_2": t2,
    }
    return trade, exit_idx


def backtest_symbol(df: pd.DataFrame, cfg, signal_fn: Signal, warmup: int = _MIN_HISTORY) -> list[dict]:
    """Replay `signal_fn` over `df` from `warmup`, simulating each fired signal.
    Non-overlapping: after a trade the scan resumes past its exit."""
    trades: list[dict] = []
    n = len(df)
    i = max(warmup, 0)
    while i < n - 1:
        sig = signal_fn(df.iloc[: i + 1])
        if not sig:
            i += 1
            continue
        level, base_height, pattern, score = sig
        a = float(atr(df.iloc[: i + 1], 14).iloc[-1])
        if a != a:   # NaN
            i += 1
            continue
        # Entry is the signal bar itself (filled at its close), not the next open.
        trade, exit_idx = simulate_trade(df, i, level, a, base_height, cfg)
        if trade is None:
            i += 1
            continue
        trade["pattern"], trade["score"] = pattern, score
        trades.append(trade)
        i = exit_idx + 1
    return trades


def default_signal(cfg) -> Signal:
    """The real detector-based trigger, mirroring the morning-scan gate."""
    from breakout.analysis.indicators import add_standard_indicators
    from breakout.analysis.patterns import detect_all
    from breakout.analysis.stage import Stage, classify_stage
    from breakout.jobs.preclose_scan import close_in_range
    from breakout.scoring import ScoringFeatures, composite_score, scoring_params

    def fn(sub: pd.DataFrame):
        if len(sub) < _MIN_HISTORY:
            return None
        d = add_standard_indicators(sub)
        stage = classify_stage(
            d,
            sma_bars=cfg.stage_filter.sma_weeks * 5,
            slope_lookback_bars=cfg.stage_filter.slope_lookback_weeks * 5,
            high_lookback_bars=cfg.stage_filter.high_lookback_weeks * 5,
        )
        if stage != Stage.STAGE_2:
            return None
        matches = detect_all(d, cfg.patterns.enabled)
        if not matches:
            return None
        top = matches[0]
        if top.breakout_level <= 0:
            return None
        close = float(d["close"].iloc[-1])
        if (top.breakout_level - close) / top.breakout_level > cfg.thresholds.near_breakout_pct / 100.0:
            return None
        vr = d["volume_ratio_20"].iloc[-1]
        # The backtest enters at this bar's close, so the chase penalty applies
        # exactly as it does live.
        score = composite_score(
            ScoringFeatures(
                quality_pass=True, stage=Stage.STAGE_2, earnings_blackout=False,
                pattern_match=top, volume_ratio=float(vr) if vr == vr else 1.0,
                extension_pct=(close - top.breakout_level) / top.breakout_level * 100.0,
                close_in_range=close_in_range(d.iloc[-1]),
            ),
            **scoring_params(cfg.thresholds),
        )
        if score < cfg.thresholds.min_score_to_alert:
            return None
        return (top.breakout_level, top.base_height, top.pattern_name, score)

    return fn


def run_backtest(prices: dict[str, pd.DataFrame], cfg, lookback_days: int = 60, signal_fn: Signal | None = None):
    """Backtest the last `lookback_days` across `prices`. Returns (digest, trades)."""
    from breakout.output.digest import compute_digest

    signal_fn = signal_fn or default_signal(cfg)
    all_trades: list[dict] = []
    for sym, df in prices.items():
        if len(df) < _MIN_HISTORY + 2:
            continue
        warmup = max(_MIN_HISTORY, len(df) - lookback_days)
        for t in backtest_symbol(df, cfg, signal_fn, warmup=warmup):
            t["symbol"] = sym
            all_trades.append(t)
    return compute_digest(all_trades), all_trades


CAVEATS = """\
┌─ READ THIS BEFORE BELIEVING THE NUMBERS ABOVE ────────────────────────────┐
  These are NOT an edge estimate. They are a regression check on the
  detector wiring. Sizing real capital on them would be a mistake.

  1. SURVIVORSHIP  It replays *today's* NIFTY 500 over history. Every name in
     the sample is one that survived and stayed in the index; the ones that
     collapsed out of it are simply absent. This inflates results and cannot
     be fixed cheaply — historical index membership isn't freely available.
  2. NON-OVERLAPPING  After a trade closes the scan resumes past its exit, so
     any signal firing while a position was held is discarded. The sample is
     therefore biased toward periods following quick exits.
  3. NO RS OR SECTOR  Both need the whole universe as of each historical day.
     They score 0 and 'flat' here, so backtest scores sit BELOW live scores —
     the same setup clears `min_score_to_alert` live but may not here.
  4. NO REGIME GATE  Needs point-in-time breadth across the universe.
  5. NO CONCENTRATION LIMITS  Every signal is taken; the live scanner caps
     total and per-sector positions, so it would not have taken them all.

  Costs, gap-through-stop fills and the capital cap on position size ARE
  modelled, and match the live tracker exactly.

  The forward paper-trade log is the edge estimate:
      python -m breakout.output.digest
└───────────────────────────────────────────────────────────────────────────┘"""


def caveats() -> str:
    """The health warning printed under every backtest run."""
    return CAVEATS


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the scanner over recent history")
    parser.add_argument("--days", type=int, default=60, help="trading days to replay")
    args = parser.parse_args()

    from breakout.config import ensure_runtime_dirs, load_config
    from breakout.data.store import Store
    from breakout.logging_setup import setup_logging
    from breakout.output.digest import render_digest

    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    with Store(cfg.paths.workbook) as store:
        prices = {u["symbol"]: store.read_prices(u["symbol"]) for u in store.read_universe()}
    digest, trades = run_backtest(prices, cfg, lookback_days=args.days)
    logger.info(f"backtest: {len(trades)} simulated trades over ~{args.days} days")
    print(render_digest(digest))
    # Printed every run, after the numbers rather than before them, so it is
    # read by someone who has just seen a win rate and is deciding what it means.
    print()
    print(caveats())


if __name__ == "__main__":
    main()
