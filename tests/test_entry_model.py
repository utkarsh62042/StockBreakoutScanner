"""The 3 PM entry model.

A confirmed breakout is bought in the 3:00-3:20 PM window on the confirmation
day, so every number on the trade row must be derived from the price the scan
saw at 3 PM — not from the breakout level, which is usually well below it by
the time the breakout is confirmed. These tests pin that down, because getting
it wrong silently inflates every statistic the paper log produces.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from breakout.config import CostsConfig
from breakout.paper.tracker import (
    TradeState,
    compute_stop,
    compute_targets,
    insert_alert,
    max_position_value,
    position_size,
    settle_one_trade,
)

CFG = SimpleNamespace(
    paper_trading=SimpleNamespace(
        atr_stop_multiplier=1.5,
        target_1_r_multiple=2.0,
        hold_max_days=10,
        alert_ttl_days=2,
    ),
    risk=SimpleNamespace(capital=200000.0, risk_per_trade_pct=2.0,
                         max_concurrent_positions=8),
    # Costs off here so the entry-model arithmetic stays exact; the cost model
    # has its own tests in test_costs.py.
    costs=CostsConfig(enabled=False),
)

LEVEL = 210.0
PRECLOSE = 220.0   # the move ran 10 points past the level before 3 PM
ATR = 4.0
STOP = LEVEL - 1.5 * ATR   # 204.0 — anchored to the level, not the entry


class FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_paper_trade(self, row: dict) -> int:
        row = dict(row, id=len(self.rows) + 1)
        self.rows.append(row)
        return row["id"]


def _insert(entry_price: float = PRECLOSE, base_height: float = 30.0) -> dict:
    store = FakeStore()
    insert_alert(
        store,
        symbol="DEMO",
        pattern="flat_base",
        alert_type="BREAKOUT",
        score=75.0,
        breakout_level=LEVEL,
        entry_price=entry_price,
        atr=ATR,
        base_height=base_height,
        cfg=CFG,
        alert_date=date(2026, 9, 10),
    )
    return store.rows[0]


def test_entry_is_the_preclose_price_not_the_breakout_level() -> None:
    row = _insert()
    assert row["entry_price"] == pytest.approx(PRECLOSE)
    assert row["entry_price"] != pytest.approx(LEVEL)


def test_trade_opens_already_entered_on_the_confirmation_day() -> None:
    row = _insert()
    assert row["state"] == TradeState.ENTERED
    assert row["entry_date"] == "2026-09-10"
    assert row["alert_date"] == row["entry_date"]


def test_stop_stays_anchored_to_the_breakout_level() -> None:
    """Anchoring the stop to the entry instead would put it at 214 — above the
    breakout level, so a routine retest of 210 would stop the trade out."""
    row = _insert()
    assert row["stop_loss"] == pytest.approx(STOP)
    assert row["stop_loss"] < LEVEL


def test_targets_measure_from_what_we_paid() -> None:
    row = _insert(base_height=30.0)
    risk = PRECLOSE - STOP   # 16.0, not the 6.0 an entry at the level implied
    assert row["target_1"] == pytest.approx(PRECLOSE + 2.0 * risk)
    assert row["target_2"] == pytest.approx(PRECLOSE + 30.0)


def test_realised_rr_is_not_inflated_by_the_gap_above_the_level() -> None:
    """The 2R target must be 2R away from the entry. Under the old model the
    row promised target_1 = 222 on a 220 entry — a 0.125R trade sold as 2R."""
    row = _insert()
    reward = row["target_1"] - PRECLOSE
    risk = PRECLOSE - row["stop_loss"]
    assert reward / risk == pytest.approx(2.0)


def test_position_size_shrinks_as_the_entry_extends_past_the_level() -> None:
    """Chasing a breakout is genuinely riskier per share, so the same 2% of
    capital has to buy fewer shares."""
    at_level = _insert(entry_price=LEVEL + 0.5)
    extended = _insert(entry_price=PRECLOSE)
    assert extended["shares"] < at_level["shares"]
    assert extended["shares"] == position_size(
        CFG.risk.capital, CFG.risk.risk_per_trade_pct, PRECLOSE, STOP,
        max_value=max_position_value(
            CFG.risk.capital, CFG.risk.max_concurrent_positions
        ),
    )


def test_a_position_never_exceeds_its_share_of_capital() -> None:
    """Risk-based sizing alone has no idea what a position *costs*. Before the
    cap, this ₹220 entry with a ₹16 stop bought 250 shares — ₹55,000, 27% of a
    ₹2,00,000 account, on a strategy meant to hold eight at once."""
    row = _insert()
    cap = max_position_value(CFG.risk.capital, CFG.risk.max_concurrent_positions)
    assert row["shares"] * row["entry_price"] <= cap + row["entry_price"]


def test_row_matches_the_standalone_helpers() -> None:
    """The alert table and the paper row are built from the same helpers, so
    the trader sees exactly the numbers that get scored."""
    row = _insert()
    stop = compute_stop(LEVEL, ATR, CFG.paper_trading.atr_stop_multiplier)
    t1, t2 = compute_targets(PRECLOSE, stop, 30.0, CFG.paper_trading.target_1_r_multiple)
    assert (row["stop_loss"], row["target_1"], row["target_2"]) == pytest.approx((stop, t1, t2))


def test_extension_is_recorded_in_notes() -> None:
    row = _insert()
    assert "entry_at_preclose=220.00" in row["notes"]
    assert "extension=+4.76%" in row["notes"]


# ── Settle behaviour on the entry day ────────────────────────────────────


def _trade(**over) -> dict:
    row = _insert()
    return dict(row, **over)


def test_entry_day_range_cannot_stop_us_out() -> None:
    """The day's low happened before our 3 PM fill."""
    ohlc = {"open": 205.0, "high": 221.0, "low": 200.0, "close": PRECLOSE}
    assert settle_one_trade(_trade(), ohlc, date(2026, 9, 10), CFG) is None


def test_entry_day_range_cannot_hand_us_a_target() -> None:
    ohlc = {"open": 205.0, "high": 300.0, "low": 205.0, "close": PRECLOSE}
    assert settle_one_trade(_trade(), ohlc, date(2026, 9, 10), CFG) is None


def test_exit_checks_resume_the_next_session() -> None:
    ohlc = {"open": 219.0, "high": 220.0, "low": 200.0, "close": 203.0}
    outcome = settle_one_trade(_trade(), ohlc, date(2026, 9, 11), CFG)
    assert outcome is not None
    assert outcome.new_state == TradeState.STOPPED_OUT
    assert outcome.exit_price == pytest.approx(STOP)
    assert outcome.pnl_r == pytest.approx(-1.0)


def test_legacy_alerted_rows_are_skipped_not_crashed() -> None:
    """Rows written by the old next-day-open model have no entry_price."""
    stale = _trade(state=TradeState.ALERTED, entry_price=None, entry_date=None)
    ohlc = {"open": 219.0, "high": 220.0, "low": 200.0, "close": 203.0}
    assert settle_one_trade(stale, ohlc, date(2026, 9, 11), CFG) is None


def test_time_exit_counts_from_the_confirmation_day() -> None:
    later = date(2026, 9, 10) + timedelta(days=CFG.paper_trading.hold_max_days)
    ohlc = {"open": 221.0, "high": 222.0, "low": 219.0, "close": 221.0}
    outcome = settle_one_trade(_trade(), ohlc, later, CFG)
    assert outcome is not None
    assert outcome.new_state == TradeState.TIME_EXIT
    assert outcome.days_held == CFG.paper_trading.hold_max_days
