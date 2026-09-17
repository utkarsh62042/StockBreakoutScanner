"""Per-alert feature logging and the feature-vs-outcome report.

The scanner weights six signals but has never recorded what they were when an
alert fired, so no amount of paper trading could say whether any of them
predicts anything. These tests pin the record (`alert_features`, joined to the
trade outcome) and the analysis over it.
"""

from __future__ import annotations

import pytest

from breakout.data.store import Store
from breakout.jobs.preclose_scan import _num, _rescore, close_in_range
from breakout.paper.stats import (
    by_categorical_feature,
    by_numeric_feature,
    feature_report,
)


# ── Storage and the outcome join ─────────────────────────────────────────


def _feature_row(trade_id: int, **over) -> dict:
    row = {
        "trade_id": trade_id, "symbol": "ACME", "alert_date": "2026-09-11",
        "alert_type": "BREAKOUT", "pattern": "darvas_box", "score": 72.0,
        "pattern_confidence": 80.0, "stage": "STAGE_2", "rs_percentile": 88.0,
        "rs_points": 15.0, "tightness": 0.6, "volume_ratio": 2.4,
        "sector_trend": "up", "distance_pct": 0.8, "extension_pct": 1.2,
        "close_in_range": 0.9, "atr_pct": 2.1, "vix": 13.4,
        "market_mood": "risk_on",
    }
    row.update(over)
    return row


def test_features_round_trip_and_join_to_the_outcome(tmp_path) -> None:
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        trade_id = store.insert_paper_trade(
            {"symbol": "ACME", "state": "TARGET_HIT", "pnl_r": 2.0, "pnl_inr": 4000.0}
        )
        store.insert_alert_features(_feature_row(trade_id))

    with Store(wb) as store:
        (joined,) = store.read_alerts_with_outcomes()

    assert joined["trade_id"] == trade_id
    assert joined["rs_percentile"] == pytest.approx(88.0)
    assert joined["close_in_range"] == pytest.approx(0.9)
    # The outcome arrives from paper_trades, not from the feature row.
    assert joined["state"] == "TARGET_HIT"
    assert joined["pnl_r"] == pytest.approx(2.0)


def test_features_upsert_on_trade_id(tmp_path) -> None:
    """Re-running the pre-close scan the same day must not double-count."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.insert_alert_features(_feature_row(1, score=60.0))
        store.insert_alert_features(_feature_row(1, score=71.0))
        rows = store.read_alert_features()

    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(71.0)


def test_open_trade_has_no_outcome_yet(tmp_path) -> None:
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        trade_id = store.insert_paper_trade({"symbol": "ACME", "state": "ENTERED"})
        store.insert_alert_features(_feature_row(trade_id))
        (joined,) = store.read_alerts_with_outcomes()

    assert joined["state"] == "ENTERED"
    assert joined["pnl_r"] is None


def test_setup_watchlist_carries_feature_columns(tmp_path) -> None:
    """The morning's features must survive to the pre-close scan as values, not
    as a string in `notes` that would have to be re-parsed."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.replace_setup_watchlist(
            [{
                "symbol": "ACME", "pattern": "vcp", "breakout_level": 100.0,
                "score": 68.0, "rs_percentile": 91.0, "rs_points": 15.0,
                "tightness": 0.7, "pattern_confidence": 75.0,
                "sector_trend": "up", "vix": 12.0,
            }]
        )
    with Store(wb) as store:
        (row,) = store.read_setup_watchlist()

    assert row["rs_percentile"] == pytest.approx(91.0)
    assert row["tightness"] == pytest.approx(0.7)
    assert row["sector_trend"] == "up"


# ── Confirmation-day features ────────────────────────────────────────────


def test_close_in_range_distinguishes_a_strong_close_from_a_weak_one() -> None:
    strong = {"high": 110.0, "low": 100.0, "close": 109.0}
    weak = {"high": 110.0, "low": 100.0, "close": 101.0}
    assert close_in_range(strong) == pytest.approx(0.9)
    assert close_in_range(weak) == pytest.approx(0.1)


def test_close_in_range_handles_a_zero_range_bar() -> None:
    assert close_in_range({"high": 100.0, "low": 100.0, "close": 100.0}) is None


# ── Rescoring from the stored features ───────────────────────────────────


def test_rescore_uses_the_real_scorer_over_the_stored_features() -> None:
    row = {
        "pattern": "darvas_box", "pattern_confidence": 80.0,
        "breakout_level": 100.0, "base_height": 10.0, "score": 68.0,
        "rs_points": 15.0, "tightness": 0.6, "sector_trend": "up",
    }
    # pattern 80*0.35=28, stage 20, rs 15, volume 15 (3.0x saturates the scale),
    # tightness 6, sector 5 => 89
    assert _rescore(row, vol_ratio=3.0) == pytest.approx(89.0)


def _nr7_row() -> dict:
    return {
        "pattern": "nr7", "pattern_confidence": 60.0, "breakout_level": 100.0,
        "base_height": 5.0, "rs_points": 0.0, "tightness": 0.0,
        "sector_trend": "flat", "score": 50.0,
    }


def test_rescore_reflects_the_confirmation_volume() -> None:
    """A 3x breakout must outrank a 1.5x one.

    This test previously pinned the opposite — volume saturated at exactly the
    confirmation gate, so every alert scored the full 15 and the component
    contributed nothing to the ranking. The scale now runs 1.5x -> 3.0x.
    """
    weak = _rescore(_nr7_row(), vol_ratio=1.5)
    strong = _rescore(_nr7_row(), vol_ratio=3.0)
    assert strong > weak
    assert strong - weak == pytest.approx(7.5)   # half the 15-point weight


def test_rescore_volume_is_monotonic_across_the_gate() -> None:
    """No jump at the knee: a candidate crossing 1.5x doesn't lurch in rank."""
    scores = [_rescore(_nr7_row(), vol_ratio=v) for v in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)]
    assert scores == sorted(scores)
    assert all(b - a > 0 for a, b in zip(scores, scores[1:]))


def test_rescore_volume_stops_rewarding_past_saturation() -> None:
    """A 10x volume spike is usually news, not accumulation — no extra credit."""
    assert _rescore(_nr7_row(), vol_ratio=10.0) == pytest.approx(
        _rescore(_nr7_row(), vol_ratio=3.0)
    )


def test_rescore_falls_back_to_the_stored_score_for_a_legacy_row() -> None:
    # Rows written before the feature columns existed have no confidence.
    assert _rescore({"score": 63.5}, vol_ratio=2.0) == pytest.approx(63.5)


def test_num_tolerates_blank_cells() -> None:
    assert _num(None) == 0.0
    assert _num(float("nan")) == 0.0
    assert _num("") == 0.0
    assert _num(2.5) == pytest.approx(2.5)


# ── Feature-vs-outcome report ────────────────────────────────────────────


def _alert(pnl_r: float | None, **features) -> dict:
    row = {"pnl_r": pnl_r, "pnl_inr": (pnl_r or 0) * 1000, "days_held": 5}
    row.update(features)
    return row


def test_numeric_feature_splits_into_terciles() -> None:
    rows = [_alert(float(i % 3 - 1), tightness=i / 10.0) for i in range(12)]
    groups = by_numeric_feature(rows, "tightness")
    assert len(groups) == 3
    assert sum(s.n for s in groups.values()) == 12


def test_a_predictive_feature_shows_a_spread() -> None:
    # Losers at low tightness, winners at high — the shape a real signal makes.
    rows = [_alert(-1.0, tightness=0.1) for _ in range(6)]
    rows += [_alert(2.0, tightness=0.9) for _ in range(6)]
    groups = by_numeric_feature(rows, "tightness")
    stats = [s for s in groups.values() if s.n]
    assert stats[0].avg_r < 0 < stats[-1].avg_r


def test_unsettled_alerts_are_excluded() -> None:
    rows = [_alert(None, tightness=0.5) for _ in range(5)]
    assert by_numeric_feature(rows, "tightness") == {}


def test_a_constant_feature_is_not_split() -> None:
    rows = [_alert(1.0, vix=13.0) for _ in range(8)]
    groups = by_numeric_feature(rows, "vix")
    assert list(groups) == ["vix=all"]


def test_categorical_feature_groups_by_value() -> None:
    rows = [_alert(2.0, sector_trend="up"), _alert(-1.0, sector_trend="down")]
    groups = by_categorical_feature(rows, "sector_trend")
    assert set(groups) == {"up", "down"}
    assert groups["up"].avg_r == pytest.approx(2.0)


def test_feature_report_drops_single_valued_categoricals() -> None:
    rows = [_alert(1.0, sector_trend="up", tightness=i / 10.0) for i in range(8)]
    rep = feature_report(rows)
    # Every alert was in an 'up' sector, so the split says nothing.
    assert "sector_trend" not in rep
    assert "tightness" in rep
