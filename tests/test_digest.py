"""Paper-trade digest tests."""

from __future__ import annotations

import math

from breakout.output.digest import compute_digest, render_digest


def _t(pattern: str, pnl_r: float | None, pnl_inr: float | None, days: int | None = 5) -> dict:
    return {"pattern": pattern, "pnl_r": pnl_r, "pnl_inr": pnl_inr, "days_held": days}


def test_overall_metrics() -> None:
    trades = [
        _t("nr7", 2.0, 2000.0),
        _t("nr7", -1.0, -1000.0),
        _t("darvas_box", 1.0, 1000.0),
        _t("darvas_box", -1.0, -1000.0),
    ]
    o = compute_digest(trades)["overall"]
    assert o.n == 4
    assert o.wins == 2 and o.losses == 2
    assert o.win_rate == 50.0
    assert o.avg_r == (2 - 1 + 1 - 1) / 4
    # gross profit 3000 / gross loss 2000
    assert o.profit_factor == (3000.0 / 2000.0)


def test_open_and_canceled_trades_excluded() -> None:
    trades = [
        _t("nr7", 2.0, 2000.0),
        _t("nr7", None, None),        # open / never settled
        {"pattern": "flag", "state": "CANCELED"},   # no pnl
    ]
    assert compute_digest(trades)["overall"].n == 1


def test_by_pattern_breakdown() -> None:
    trades = [_t("nr7", 2.0, 2000.0), _t("nr7", 1.0, 1000.0), _t("flag", -1.0, -1000.0)]
    bp = compute_digest(trades)["by_pattern"]
    assert bp["nr7"].n == 2 and bp["nr7"].win_rate == 100.0
    assert bp["flag"].n == 1 and bp["flag"].wins == 0


def test_profit_factor_all_wins_is_infinite() -> None:
    o = compute_digest([_t("nr7", 2.0, 2000.0), _t("nr7", 1.0, 1000.0)])["overall"]
    assert math.isinf(o.profit_factor)


def test_empty_is_zeroed() -> None:
    o = compute_digest([])["overall"]
    assert o.n == 0 and o.profit_factor is None
    assert "settled trades : 0" in render_digest({"overall": o, "by_pattern": {}})


def test_render_contains_key_lines() -> None:
    text = render_digest(compute_digest([_t("nr7", 2.0, 2000.0)]))
    assert "win rate" in text and "profit factor" in text and "nr7" in text
