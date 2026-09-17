"""Market-regime gate.

Breakouts concentrate their losses in risk-off tape, so this is the largest
accuracy lever left. The design decision these tests pin down is that the gate
*sizes down* rather than switching off: a hard on/off would discard exactly the
observations needed to tell whether the gate was right.
"""

from __future__ import annotations

import pandas as pd
import pytest

from breakout.config import RegimeConfig
from breakout.filters.regime import (
    assess_regime,
    market_breadth,
    pct_above_sma,
)
from breakout.paper.tracker import (
    insert_alert,
    max_position_value,
    position_size,
)

CFG = RegimeConfig()
OFF = RegimeConfig(enabled=False)


def _index(direction: str, n: int = 300) -> pd.Series:
    """A synthetic index series that is clearly rising, falling, or flat."""
    if direction == "up":
        return pd.Series([100.0 + i * 0.5 for i in range(n)])
    if direction == "down":
        return pd.Series([100.0 + (n - i) * 0.5 for i in range(n)])
    return pd.Series([100.0] * n)


# ── Components ───────────────────────────────────────────────────────────


def test_pct_above_sma_detects_both_sides() -> None:
    assert pct_above_sma(_index("up"), 50) is True
    assert pct_above_sma(_index("down"), 50) is False


def test_pct_above_sma_is_none_on_short_history() -> None:
    """Symbols without 50 bars must not be counted as "below" — that would
    bias breadth down every time the universe refreshes with new listings."""
    assert pct_above_sma(pd.Series([100.0] * 20), 50) is None


def test_market_breadth_is_a_percentage() -> None:
    assert market_breadth(150, 500) == pytest.approx(30.0)
    assert market_breadth(0, 0) is None


# ── The combined assessment ──────────────────────────────────────────────


def test_all_three_positive_is_risk_on_at_full_size() -> None:
    r = assess_regime(_index("up"), 75.0, "risk_on", CFG)
    assert (r.label, r.score) == ("risk_on", 3)
    assert r.risk_multiplier == 1.0


def test_risk_on_never_sizes_above_baseline() -> None:
    """A regime filter that can increase size is a leverage knob, not a filter."""
    for breadth in (61.0, 80.0, 100.0):
        assert assess_regime(_index("up"), breadth, "risk_on", CFG).risk_multiplier <= 1.0


def test_two_of_three_negative_halves_the_size() -> None:
    r = assess_regime(_index("down"), 30.0, "neutral", CFG)
    assert (r.label, r.score) == ("risk_off", -2)
    assert r.risk_multiplier == pytest.approx(CFG.risk_off_multiplier)


def test_all_three_negative_is_treated_as_worse_than_two() -> None:
    severe = assess_regime(_index("down"), 20.0, "risk_off", CFG)
    assert severe.score == -3
    assert severe.risk_multiplier == pytest.approx(CFG.severe_multiplier)
    assert severe.risk_multiplier < CFG.risk_off_multiplier


def test_mixed_signals_are_neutral_at_full_size() -> None:
    r = assess_regime(_index("up"), 30.0, "neutral", CFG)
    assert (r.label, r.score, r.risk_multiplier) == ("neutral", 0, 1.0)


def test_missing_index_data_does_not_penalise_sizing() -> None:
    """A failed fetch is not evidence of a bad tape."""
    r = assess_regime(None, 50.0, "neutral", CFG)
    assert (r.nifty_trend, r.score, r.risk_multiplier) == ("flat", 0, 1.0)


def test_missing_breadth_does_not_penalise_sizing() -> None:
    r = assess_regime(_index("up"), None, "risk_on", CFG)
    assert r.score == 2 and r.risk_multiplier == 1.0


def test_disabled_still_reports_but_never_resizes() -> None:
    """Off means "don't act on it", not "don't measure it" — the label keeps
    accumulating in alert_features so the thresholds can be calibrated."""
    r = assess_regime(_index("down"), 20.0, "risk_off", OFF)
    assert r.label == "risk_off" and r.score == -3
    assert r.risk_multiplier == 1.0


def test_summary_mentions_each_vote() -> None:
    r = assess_regime(_index("down"), 30.0, "risk_off", CFG)
    assert "nifty=down" in r.summary() and "breadth=30%" in r.summary()
    assert set(r.reasons) == {"nifty=-1", "breadth=-1", "vix=-1"}


# ── Effect on the trade that actually gets opened ────────────────────────


class FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_paper_trade(self, row: dict) -> int:
        self.rows.append(row)
        return len(self.rows)


def _open(risk_multiplier: float) -> dict:
    from types import SimpleNamespace

    from breakout.config import CostsConfig

    cfg = SimpleNamespace(
        paper_trading=SimpleNamespace(
            atr_stop_multiplier=1.5, target_1_r_multiple=2.0, hold_max_days=30
        ),
        risk=SimpleNamespace(capital=200000.0, risk_per_trade_pct=2.5,
                         max_concurrent_positions=8),
        costs=CostsConfig(enabled=False),
    )
    store = FakeStore()
    insert_alert(
        store, symbol="DEMO", pattern="nr7", alert_type="BREAKOUT", score=70.0,
        breakout_level=210.0, entry_price=220.0, atr=4.0, base_height=30.0,
        cfg=cfg, risk_multiplier=risk_multiplier,
    )
    return store.rows[0]


def test_half_size_regime_halves_the_share_count() -> None:
    """Both sides go through the capital cap, so the comparison must too — at
    full size this setup is capped at 25,000/220 = 113 shares, not the 156 that
    pure risk-based sizing would buy."""
    cap = max_position_value(200000.0, 8)
    full, half = _open(1.0), _open(0.5)
    assert full["shares"] == position_size(200000.0, 2.5, 220.0, 204.0, max_value=cap)
    assert half["shares"] == position_size(200000.0, 1.25, 220.0, 204.0, max_value=cap)
    assert half["shares"] <= full["shares"]


def test_sizing_down_does_not_move_the_stop_or_targets() -> None:
    """Regime is about how much to bet, not about where the setup is wrong.
    A half-size trade that stops out is still exactly -1R."""
    full, half = _open(1.0), _open(0.25)
    for col in ("entry_price", "stop_loss", "target_1", "target_2"):
        assert half[col] == pytest.approx(full[col])


def test_reduced_size_is_recorded_on_the_row() -> None:
    assert "risk_x0.50" in _open(0.5)["notes"]
    assert "risk_x" not in _open(1.0)["notes"]
