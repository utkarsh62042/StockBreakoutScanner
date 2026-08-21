"""Backtest engine tests (pure, no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from breakout.backtest import backtest_symbol, run_backtest, simulate_trade
from breakout.paper.tracker import TradeState

CFG = SimpleNamespace(
    paper_trading=SimpleNamespace(atr_stop_multiplier=1.5, target_1_r_multiple=2.0, hold_max_days=6),
    risk=SimpleNamespace(capital=200000.0, risk_per_trade_pct=2.5),
)


def _df(opens, highs, lows, closes) -> pd.DataFrame:
    n = len(opens)
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1] * n}, index=idx
    )


# ── simulate_trade ────────────────────────────────────────────────────────
# level=100, atr=2 → stop=97, risk=3. entry = open[1] = 100.


def test_stop_out() -> None:
    df = _df([100, 100, 100], [101, 101, 101], [99, 99, 96], [100, 100, 100])
    trade, exit_idx = simulate_trade(df, 1, 100.0, 2.0, 20.0, CFG)  # t2=120 (unhit)
    assert trade["state"] == TradeState.STOPPED_OUT
    assert trade["exit_price"] == pytest.approx(97.0)
    assert trade["pnl_r"] == pytest.approx(-1.0)


def test_target_hit() -> None:
    df = _df([100, 100, 100], [101, 101, 111], [99, 100, 100], [100, 100, 110])
    trade, _ = simulate_trade(df, 1, 100.0, 2.0, 10.0, CFG)  # t2 = 100 + 10 = 110
    assert trade["state"] == TradeState.TARGET_HIT
    assert trade["exit_price"] == pytest.approx(110.0)
    assert trade["pnl_r"] == pytest.approx(10.0 / 3.0)


def test_target1_moves_stop_to_breakeven() -> None:
    # High tags target_1 (106) → stop to 100; next bar dips to 99 → exit at 100 (0R).
    df = _df([100, 100, 100], [101, 107, 101], [99, 99, 99], [100, 100, 100])
    trade, _ = simulate_trade(df, 1, 100.0, 2.0, 20.0, CFG)
    assert trade["state"] == TradeState.STOPPED_OUT
    assert trade["exit_price"] == pytest.approx(100.0)
    assert trade["pnl_r"] == pytest.approx(0.0)


def test_time_exit() -> None:
    df = _df([100] * 8, [101] * 8, [99] * 8, [100, 100, 100, 100, 100, 100, 100, 103])
    trade, _ = simulate_trade(df, 1, 100.0, 2.0, 20.0, CFG)  # never stops/targets
    assert trade["state"] == TradeState.TIME_EXIT
    assert trade["exit_price"] == pytest.approx(103.0)
    assert trade["days_held"] == 6


def test_non_positive_risk_returns_none() -> None:
    df = _df([100, 80, 100], [101, 101, 101], [99, 79, 99], [100, 80, 100])  # entry 80 < stop 97
    trade, _ = simulate_trade(df, 1, 100.0, 2.0, 20.0, CFG)
    assert trade is None


# ── backtest_symbol ───────────────────────────────────────────────────────


def _series_with_breakout() -> pd.DataFrame:
    closes = [100.0] * 30
    closes[22] = 110.0
    highs = [c + 1 for c in closes]
    highs[22] = 111.0
    lows = [c - 1 for c in closes]
    opens = [100.0] * 30
    return _df(opens, highs, lows, closes)


def test_backtest_symbol_fires_once() -> None:
    df = _series_with_breakout()
    sig = lambda sub: (100.0, 10.0, "nr7", 70.0) if len(sub) == 21 else None
    trades = backtest_symbol(df, CFG, sig, warmup=15)
    assert len(trades) == 1
    assert trades[0]["pattern"] == "nr7"
    assert trades[0]["state"] == TradeState.TARGET_HIT


def test_backtest_symbol_no_signal() -> None:
    df = _series_with_breakout()
    assert backtest_symbol(df, CFG, lambda sub: None, warmup=15) == []


def test_run_backtest_shape_empty() -> None:
    digest, trades = run_backtest({"X": _series_with_breakout()}, CFG, lookback_days=60,
                                  signal_fn=lambda sub: None)
    assert "overall" in digest and trades == []
