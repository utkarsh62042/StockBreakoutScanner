"""End-to-end smoke test using a synthetic Stage-2-with-pattern fixture.

Synthesizes a NIFTY-500-like stock that ramps from 100 to 200 over 250 days
ending with a tight consolidation just below the 52-week high, runs it
through the entire morning + preclose + settle pipeline, and verifies:

  1. The morning scan adds it to the setup watchlist.
  2. The pre-close scan confirms a breakout, emits a CSV alert, and creates
     a paper trade in ALERTED state.
  3. The EOD settle transitions ALERTED -> ENTERED on the next bar.

This is the closest we can get to a full integration test without hitting
yfinance or Angel One.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from breakout.analysis.indicators import add_standard_indicators
from breakout.analysis.patterns import detect_all
from breakout.analysis.stage import Stage, classify_stage
from breakout.config import PathsConfig, load_config
from breakout.data.store import Store
from breakout.filters.quality import check_quality
from breakout.output.alerts import Alert, CSVChannel, dispatch_alerts
from breakout.paper.tracker import (
    OPEN_STATES,
    TradeState,
    apply_outcome,
    compute_stop,
    compute_targets,
    insert_alert,
    position_size,
    settle_one_trade,
)
from breakout.scoring import ScoringFeatures, composite_score


def _stage_2_with_setup() -> pd.DataFrame:
    """Build a 300-bar OHLCV series in clean Stage 2 with a setup at the end.

    Shape:
      - Bars 0-200: linear ramp from 100 to 195 (Stage 2 backbone)
      - Bars 200-249: ramp from 195 to 205 (recent push to new highs)
      - Bars 250-298: tight consolidation between 203 and 207 (deterministic)
      - Bar 299: today, sitting at 206 — within 2% of the ~207 ceiling
    """
    ramp_early = list(np.linspace(100, 195, 200))
    ramp_late = list(np.linspace(195.5, 205, 50))
    # Deterministic consolidation that prints higher closes than the
    # pre-consolidation peak (~205), so the higher-high check passes.
    consolidation = [203 + (i % 7) * 0.6 for i in range(49)]  # cycles 203 -> ~206.6
    today = [206.0]
    closes = ramp_early + ramp_late + consolidation + today
    assert len(closes) == 300

    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    # Pin the 52-week ceiling cleanly: one bar in the consolidation reaches 207.
    highs[275] = 207.0
    # Today's bar: tight range just below the 207 ceiling — within 2%.
    highs[-1] = 206.3
    lows[-1] = 205.8
    closes[-1] = 206.0
    # Volume: average for most, spike on today
    volume = [500_000.0] * 299 + [1_500_000.0]

    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
        },
        index=pd.date_range(end=date.today(), periods=300, freq="B"),
    )


def test_full_pipeline_alerts_and_persists(tmp_path: Path) -> None:
    """End-to-end: setup -> watchlist -> alert -> paper trade -> settle."""
    base_cfg = load_config()
    # Build an isolated paths config so the test never touches the user's
    # real cache / output / logs directories.
    sandbox_paths = PathsConfig(
        data_cache=tmp_path / "data_cache",
        output=tmp_path / "output",
        logs=tmp_path / "logs",
        workbook=tmp_path / "data_cache" / "test.xlsx",
    )
    cfg = replace(base_cfg, paths=sandbox_paths)
    (tmp_path / "data_cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    df_raw = _stage_2_with_setup()
    df = add_standard_indicators(df_raw)

    # ── 1. Verify the setup is properly classified ──────────────────────
    stage = classify_stage(df)
    assert stage == Stage.STAGE_2, f"expected STAGE_2, got {stage}"

    matches = detect_all(df, ["fifty_two_week_high", "darvas_box", "nr7"])
    assert matches, "expected at least one pattern match"
    top = matches[0]
    assert top.detected
    # 52-week high or NR7 should fire here
    assert top.pattern_name in {"fifty_two_week_high", "darvas_box", "nr7"}

    # ── 2. Quality + scoring ─────────────────────────────────────────────
    qual = check_quality(df, {"market_cap_cr": 50000}, cfg.thresholds)
    # ADV: avg(close*volume) over last 20 days. Close ~199, vol ~500k.
    # turnover ≈ 99.5M = ~10cr — should pass the 5cr floor.
    assert qual.passed, f"quality failed: {qual.reasons_failed}"

    features = ScoringFeatures(
        quality_pass=True,
        stage=stage,
        earnings_blackout=False,
        pattern_match=top,
        volume_ratio=3.0,  # today's spike
        rs_score=0.0,
        tightness_score=0.0,
        sector_trend="flat",
    )
    score = composite_score(features)
    assert score >= 50, f"score {score} below watchlist threshold"

    # ── 3. Persist to SQLite ────────────────────────────────────────────
    with Store(cfg.paths.workbook) as store:
        store.replace_setup_watchlist(
            [
                {
                    "symbol": "DEMO",
                    "pattern": top.pattern_name,
                    "breakout_level": top.breakout_level,
                    "score": score,
                    "detected_date": date.today().isoformat(),
                    "base_height": top.base_height,
                    "notes": "synthetic",
                }
            ]
        )
        watchlist = store.read_setup_watchlist()
        assert len(watchlist) == 1
        assert watchlist[0]["symbol"] == "DEMO"

        # ── 4. Pre-close confirmation: paper trade in ALERTED ────────────
        atr_14 = 1.0  # synthetic — bars are tight
        trade_id = insert_alert(
            store,
            symbol="DEMO",
            pattern=top.pattern_name,
            alert_type="BREAKOUT",
            score=score,
            breakout_level=top.breakout_level,
            atr=atr_14,
            base_height=top.base_height,
            cfg=cfg,
        )
        assert trade_id > 0

        # ── 5. Emit CSV alert ───────────────────────────────────────────
        entry = top.breakout_level  # at the level
        stop = compute_stop(entry, atr_14, cfg.paper_trading.atr_stop_multiplier)
        t1, t2 = compute_targets(entry, stop, top.base_height)
        shares = position_size(cfg.risk.capital, cfg.risk.risk_per_trade_pct, entry, stop)
        assert shares > 0

        alert = Alert(
            symbol="DEMO", score=score, pattern=top.pattern_name,
            breakout_level=top.breakout_level, entry_price=entry,
            stop_loss=stop, target_1=t1, target_2=t2,
            position_size=shares, volume_ratio=3.0, stage="STAGE_2",
            alert_type="BREAKOUT",
        )
        csv_channel = CSVChannel(cfg.paths.output)
        dispatch_alerts([alert], [csv_channel], run_date=date.today())

        csv_files = list(cfg.paths.output.glob("alerts_*.csv"))
        assert len(csv_files) == 1, f"expected 1 CSV, got {csv_files}"
        assert csv_files[0].read_text(encoding="utf-8").count("DEMO") >= 1

        # ── 6. EOD settle: ALERTED -> ENTERED ────────────────────────────
        trade = store.read_paper_trades_by_state(*OPEN_STATES)[0]
        assert trade["state"] == TradeState.ALERTED
        next_day = date.today() + timedelta(days=1)
        tomorrow_ohlc = {
            "open": entry * 1.005,
            "high": entry * 1.02,
            "low": entry * 0.995,
            "close": entry * 1.015,
        }
        outcome = settle_one_trade(trade, tomorrow_ohlc, next_day, cfg)
        assert outcome is not None
        assert outcome.new_state == TradeState.ENTERED
        apply_outcome(store, trade["id"], outcome)

        # Trade is now ENTERED with entry_price and entry_date set
        entered = store.read_paper_trades_by_state(TradeState.ENTERED)
        assert len(entered) == 1
        assert entered[0]["entry_price"] == pytest.approx(entry * 1.005)
