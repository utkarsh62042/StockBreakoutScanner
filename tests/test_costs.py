"""Gap-through-stop fills and transaction costs.

Both exist for the same reason: the paper log is the number that decides
whether real capital goes in, and both of these biased it in the optimistic
direction. A stop assumed to fill exactly at the stop overstates R on the
gap-down trades that produce the fat left tail; unmodelled costs overstate
every trade by 0.2-0.35% of turnover.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from breakout.backtest import simulate_trade
from breakout.config import CostsConfig
from breakout.output.digest import compute_digest, render_digest
from breakout.paper.costs import cost_per_share, round_trip_cost, settle_pnl
from breakout.paper.tracker import TradeState, settle_one_trade

COSTS = CostsConfig()          # production defaults
FREE = CostsConfig(enabled=False)


def _cfg(costs: CostsConfig = COSTS) -> SimpleNamespace:
    return SimpleNamespace(
        paper_trading=SimpleNamespace(
            atr_stop_multiplier=1.5, target_1_r_multiple=2.0, hold_max_days=10
        ),
        risk=SimpleNamespace(capital=200000.0, risk_per_trade_pct=2.0,
                         max_concurrent_positions=8),
        costs=costs,
    )


ENTRY, STOP, TARGET_2 = 220.0, 204.0, 250.0


def _trade(**over) -> dict:
    row = {
        "id": 1, "symbol": "DEMO", "state": TradeState.ENTERED,
        "entry_date": "2026-09-10", "entry_price": ENTRY, "stop_loss": STOP,
        "target_1": 252.0, "target_2": TARGET_2, "shares": 25,
    }
    return dict(row, **over)


NEXT_DAY = date(2026, 9, 11)


# ── Gap-through-stop fills ───────────────────────────────────────────────


def test_gap_down_through_the_stop_fills_at_the_open() -> None:
    """Opening at 190 with a 204 stop means 204 was never available."""
    ohlc = {"open": 190.0, "high": 195.0, "low": 188.0, "close": 191.0}
    outcome = settle_one_trade(_trade(), ohlc, NEXT_DAY, _cfg(FREE))
    assert outcome.new_state == TradeState.STOPPED_OUT
    assert outcome.exit_price == pytest.approx(190.0)
    assert outcome.pnl_r < -1.0          # worse than the nominal 1R risk
    assert "stop_gap_open" in outcome.notes


def test_intraday_stop_still_fills_at_the_stop() -> None:
    """Opening above the stop and trading down through it is a normal fill."""
    ohlc = {"open": 219.0, "high": 220.0, "low": 200.0, "close": 203.0}
    outcome = settle_one_trade(_trade(), ohlc, NEXT_DAY, _cfg(FREE))
    assert outcome.exit_price == pytest.approx(STOP)
    assert outcome.pnl_r == pytest.approx(-1.0)
    assert outcome.notes == "stop_hit_intraday"


def test_gap_up_through_the_target_still_books_the_target() -> None:
    """The conservative direction on the winning side — no change wanted."""
    ohlc = {"open": 270.0, "high": 275.0, "low": 268.0, "close": 272.0}
    outcome = settle_one_trade(_trade(), ohlc, NEXT_DAY, _cfg(FREE))
    assert outcome.new_state == TradeState.TARGET_HIT
    assert outcome.exit_price == pytest.approx(TARGET_2)


def test_backtest_agrees_with_the_tracker_on_gap_fills() -> None:
    """If these two disagree the backtest stops being a check on the tracker."""
    idx = pd.date_range("2026-01-01", periods=3, freq="B")
    df = pd.DataFrame(
        {"open": [100, 100, 90], "high": [101, 101, 95], "low": [99, 99, 88],
         "close": [100, 100, 91], "volume": [1, 1, 1]},
        index=idx,
    )
    # level=100, atr=2 → stop 97; bar 2 opens at 90, well through it.
    trade, _ = simulate_trade(df, 1, 100.0, 2.0, 20.0, _cfg(FREE))
    assert trade["state"] == TradeState.STOPPED_OUT
    assert trade["exit_price"] == pytest.approx(90.0)
    assert trade["pnl_r"] == pytest.approx(-10.0 / 3.0)


# ── Transaction costs ────────────────────────────────────────────────────


def test_round_trip_cost_is_in_the_expected_band() -> None:
    """~0.2-0.35% of round-trip turnover, plus the slippage haircut."""
    entry, exit_, shares = 220.0, 230.0, 25
    cost = round_trip_cost(entry, exit_, shares, COSTS)
    turnover = entry * shares + exit_ * shares
    assert 0.002 < cost / turnover < 0.0045


def test_costs_reduce_both_rupees_and_r() -> None:
    gross, costs_inr, net, net_r = settle_pnl(220.0, 230.0, 25, 204.0, COSTS)
    assert gross == pytest.approx(250.0)
    assert costs_inr > 0
    assert net == pytest.approx(gross - costs_inr)
    gross_r = (230.0 - 220.0) / (220.0 - 204.0)
    assert net_r < gross_r


def test_costs_make_a_losing_trade_worse_not_better() -> None:
    """Sign handling: costs are always a drag, never an offset."""
    _, costs_inr, net, net_r = settle_pnl(220.0, 210.0, 25, 204.0, COSTS)
    assert net < -250.0 and costs_inr > 0 and net_r < 0


def test_a_marginal_winner_can_go_net_negative() -> None:
    """The point of the whole exercise — a +0.1% move does not pay for itself."""
    _, _, net, _ = settle_pnl(220.0, 220.22, 25, 204.0, COSTS)
    assert net < 0


def test_disabled_costs_are_exactly_zero() -> None:
    gross, costs_inr, net, net_r = settle_pnl(220.0, 230.0, 25, 204.0, FREE)
    assert (costs_inr, net) == (0.0, pytest.approx(gross))
    assert net_r == pytest.approx(10.0 / 16.0)
    assert cost_per_share(220.0, 230.0, FREE) == 0.0


def test_zero_share_position_costs_nothing() -> None:
    assert round_trip_cost(220.0, 230.0, 0, COSTS) == 0.0


def test_settle_writes_gross_and_costs_alongside_net() -> None:
    ohlc = {"open": 219.0, "high": 220.0, "low": 200.0, "close": 203.0}
    outcome = settle_one_trade(_trade(), ohlc, NEXT_DAY, _cfg(COSTS))
    assert outcome.gross_pnl_inr == pytest.approx((STOP - ENTRY) * 25)
    assert outcome.costs_inr > 0
    assert outcome.pnl_inr == pytest.approx(outcome.gross_pnl_inr - outcome.costs_inr)
    assert outcome.pnl_r < -1.0


# ── Digest reporting ─────────────────────────────────────────────────────


def test_digest_reports_gross_net_and_the_drag() -> None:
    trades = [
        {"pattern": "nr7", "pnl_r": 1.8, "pnl_inr": 900.0,
         "gross_pnl_inr": 1000.0, "costs_inr": 100.0, "days_held": 5},
        {"pattern": "nr7", "pnl_r": -1.1, "pnl_inr": -560.0,
         "gross_pnl_inr": -500.0, "costs_inr": 60.0, "days_held": 3},
    ]
    o = compute_digest(trades)["overall"]
    assert o.net_inr == pytest.approx(340.0)
    assert o.gross_inr == pytest.approx(500.0)
    assert o.costs_inr == pytest.approx(160.0)
    assert "32% of gross" in render_digest(compute_digest(trades))


def test_digest_tolerates_trades_settled_before_costs_existed() -> None:
    """Old rows carry only `pnl_inr`; gross must not read as zero."""
    o = compute_digest([{"pattern": "nr7", "pnl_r": 1.0, "pnl_inr": 500.0}])["overall"]
    assert o.net_inr == pytest.approx(500.0)
    assert o.gross_inr == pytest.approx(500.0)
    assert o.costs_inr == 0.0
