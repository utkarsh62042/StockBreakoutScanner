"""paper/stats.py analytics tests."""

from __future__ import annotations

from datetime import date, timedelta

from breakout.paper.stats import by_score_band, by_sector, filter_period, full_report


def _t(symbol, score, pnl_r, pnl_inr, exit_date="2026-08-01", pattern="nr7"):
    return {
        "symbol": symbol, "score": score, "pnl_r": pnl_r, "pnl_inr": pnl_inr,
        "exit_date": exit_date, "pattern": pattern, "days_held": 5,
    }


def test_by_score_band_groups() -> None:
    trades = [_t("A", 65, 2.0, 2000.0), _t("B", 72, -1.0, -1000.0), _t("C", 85, 1.0, 1000.0)]
    bands = by_score_band(trades)
    assert bands["60-69"].n == 1 and bands["60-69"].wins == 1
    assert bands["70-79"].n == 1 and bands["70-79"].wins == 0
    assert bands["80-89"].n == 1
    assert bands["90-100"].n == 0


def test_by_sector_groups() -> None:
    trades = [_t("HDFCBANK", 70, 1.0, 1000.0), _t("SUNPHARMA", 70, -1.0, -1000.0)]
    sector_of = {"HDFCBANK": "BANK", "SUNPHARMA": "PHARMA"}
    bs = by_sector(trades, sector_of)
    assert bs["BANK"].n == 1 and bs["BANK"].win_rate == 100.0
    assert bs["PHARMA"].wins == 0


def test_by_sector_unknown_bucket() -> None:
    bs = by_sector([_t("XYZ", 70, 1.0, 1000.0)], sector_of={})
    assert bs["unknown"].n == 1


def test_filter_period() -> None:
    # Relative to today — a hardcoded "recent" date silently expires.
    recent = (date.today() - timedelta(days=5)).isoformat()
    trades = [_t("A", 70, 1.0, 1000.0, exit_date=recent),
              _t("B", 70, 1.0, 1000.0, exit_date="2020-01-01")]
    kept = filter_period(trades, "30d")
    assert len(kept) == 1 and kept[0]["symbol"] == "A"
    assert len(filter_period(trades, "all")) == 2


def test_full_report_shape() -> None:
    rep = full_report([_t("A", 85, 2.0, 2000.0)], {"A": "IT"})
    assert set(rep) == {
        "overall", "by_pattern", "by_score_band", "by_sector", "by_feature",
    }
    assert rep["overall"].n == 1
