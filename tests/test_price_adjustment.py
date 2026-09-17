"""Split / bonus / dividend adjustment handling.

An unadjusted split puts a permanent artificial cliff in a symbol's history and
silently corrupts every signal derived from it. Two mechanisms guard against
that, and these tests pin both:

  1. `Store.upsert_prices` re-bases cached bars (and the levels on any open
     paper trade) when the feed's adjustment basis changes.
  2. `data.validate` refuses a series that still contains a discontinuity,
     which is the backstop for a feed that never adjusted at all.
"""

from __future__ import annotations

import pandas as pd
import pytest

from breakout.data.store import _OPEN_TRADE_STATES, Store
from breakout.data.validate import find_price_discontinuity, is_continuous
from breakout.paper.tracker import OPEN_STATES


def _bars(start: str, closes: list[float], volume: int = 100_000) -> pd.DataFrame:
    """OHLCV frame with a flat intrabar range, indexed by business day."""
    idx = pd.bdate_range(start, periods=len(closes), name="date")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [volume] * len(closes),
        },
        index=idx,
    )


# ── Cache re-basing ──────────────────────────────────────────────────────


def test_split_rebases_bars_outside_the_incoming_window(tmp_path) -> None:
    """The whole cached history follows the feed, not just the refetched dates.

    This is the case that a plain date-keyed upsert gets wrong: it would replace
    the overlap and leave the older bars at pre-split prices, manufacturing a
    -50% cliff at the seam.
    """
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [1000.0] * 20))
        # A 1:2 split: the feed now reports the same days at half the price and
        # double the volume. The refetch covers only the last 5 of those 20 days
        # (plus 5 new ones) — the other 15 are never sent again.
        store.upsert_prices(
            "ACME", _bars("2026-01-22", [500.0] * 10, volume=200_000)
        )
        out = store.read_prices("ACME")

    assert len(out) == 25
    # Every bar, including the 15 never refetched, is on the post-split basis.
    assert out["close"].unique().tolist() == [pytest.approx(500.0)]
    assert find_price_discontinuity(out) is None
    # Volume carries its own factor and follows the share count.
    assert out["volume"].iloc[0] == pytest.approx(200_000, rel=0.01)


def test_dividend_adjustment_moves_prices_but_not_volume(tmp_path) -> None:
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [1000.0] * 20))
        # ~1% dividend back-adjustment: prices shift, share count does not.
        store.upsert_prices("ACME", _bars("2026-01-22", [990.0] * 5))
        out = store.read_prices("ACME")

    assert out["close"].iloc[0] == pytest.approx(990.0, rel=0.001)
    assert out["volume"].iloc[0] == pytest.approx(100_000, rel=0.001)


def test_ordinary_price_movement_does_not_rebase(tmp_path) -> None:
    """The overlap must agree on *shared dates*; a trending series does not
    re-base just because its newest bars are higher."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [100.0 + i for i in range(20)]))
        before = store.read_prices("ACME")["close"].iloc[0]
        # Refetch the same window unchanged, plus new higher bars.
        store.upsert_prices("ACME", _bars("2026-01-01", [100.0 + i for i in range(25)]))
        after = store.read_prices("ACME")["close"].iloc[0]

    assert before == pytest.approx(after)


def test_single_overlapping_bar_is_not_enough_to_rebase(tmp_path) -> None:
    """One disagreeing bar is a feed glitch, not an adjustment — re-basing the
    whole history off it would be the more destructive error."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [1000.0] * 20))
        last_day = pd.bdate_range("2026-01-01", periods=20)[-1]
        store.upsert_prices("ACME", _bars(last_day.strftime("%Y-%m-%d"), [500.0]))
        out = store.read_prices("ACME")

    assert out["close"].iloc[0] == pytest.approx(1000.0)


# ── Open paper trades follow the re-basing ───────────────────────────────


def test_split_rebases_open_trade_levels(tmp_path) -> None:
    """Without this, the settle after a split reads the position as stopped out
    at ~-50% and the paper record is destroyed."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [1000.0] * 20))
        trade_id = store.insert_paper_trade(
            {
                "symbol": "ACME", "state": "ENTERED", "entry_price": 1000.0,
                "stop_loss": 950.0, "target_1": 1100.0, "target_2": 1200.0,
                "shares": 100,
            }
        )
        store.upsert_prices("ACME", _bars("2026-01-22", [500.0] * 5, volume=200_000))
        (trade,) = store.read_paper_trades_by_state("ENTERED")

    assert trade["id"] == trade_id
    assert trade["entry_price"] == pytest.approx(500.0)
    assert trade["stop_loss"] == pytest.approx(475.0)
    assert trade["target_1"] == pytest.approx(550.0)
    assert trade["target_2"] == pytest.approx(600.0)
    # Rupee risk is unchanged: 100 x 50 before, 200 x 25 after.
    assert trade["shares"] == 200


def test_closed_trades_keep_their_historical_prices(tmp_path) -> None:
    """A settled trade is a record of what happened at the prices of the day;
    re-basing it would rewrite realised P&L."""
    wb = tmp_path / "wb.xlsx"
    with Store(wb) as store:
        store.upsert_prices("ACME", _bars("2026-01-01", [1000.0] * 20))
        store.insert_paper_trade(
            {
                "symbol": "ACME", "state": "TARGET_HIT", "entry_price": 1000.0,
                "stop_loss": 950.0, "exit_price": 1200.0, "shares": 100,
            }
        )
        store.upsert_prices("ACME", _bars("2026-01-22", [500.0] * 5))
        (trade,) = store.read_paper_trades_by_state("TARGET_HIT")

    assert trade["entry_price"] == pytest.approx(1000.0)
    assert trade["exit_price"] == pytest.approx(1200.0)


def test_store_open_states_match_the_tracker() -> None:
    # The data layer duplicates these to avoid importing the paper layer.
    assert set(_OPEN_TRADE_STATES) == set(OPEN_STATES)


# ── Discontinuity backstop ───────────────────────────────────────────────


def test_unadjusted_split_is_detected() -> None:
    df = _bars("2026-01-01", [1000.0] * 10 + [500.0] * 10)
    found = find_price_discontinuity(df)
    assert found is not None
    when, move = found
    assert when == "2026-01-15"
    assert move == pytest.approx(-0.5)


def test_clean_series_has_no_discontinuity() -> None:
    df = _bars("2026-01-01", [100.0 * (1.02**i) for i in range(30)])
    assert find_price_discontinuity(df) is None
    assert is_continuous(df, "ACME")


def test_a_sharp_but_plausible_move_is_allowed() -> None:
    # -18% on results day is real price action, not a corporate action.
    df = _bars("2026-01-01", [100.0] * 10 + [82.0] * 10)
    assert find_price_discontinuity(df) is None


def test_worst_offender_is_reported_when_several_exist() -> None:
    df = _bars("2026-01-01", [100.0] * 5 + [70.0] * 5 + [20.0] * 5)
    found = find_price_discontinuity(df)
    assert found is not None
    assert found[1] == pytest.approx(20.0 / 70.0 - 1.0)


def test_short_or_empty_series_is_not_flagged() -> None:
    assert find_price_discontinuity(_bars("2026-01-01", [100.0])) is None
    assert find_price_discontinuity(pd.DataFrame()) is None
