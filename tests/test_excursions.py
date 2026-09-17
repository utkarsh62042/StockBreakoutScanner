"""max_favorable / max_adverse — how far the trade ever got, in R.

Both columns have been in the schema and documented in GUIDE § 12 since the
start, and were never written. They are the cheapest way to learn whether the
stop and the target are in the right place, and neither question is answerable
from realised P&L alone:

    max_favorable 3.5R on a trade that exited at +2R  -> target too near
    max_adverse  -0.9R on a trade that went on to win -> stop too tight
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from breakout.paper.tracker import compute_excursions

ENTRY, STOP = 100.0, 90.0        # risk = 10/share, so 1R = 10 points


def _bars(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """rows of (date, high, low)."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _, _ in rows], name="date")
    return pd.DataFrame(
        {"high": [h for _, h, _ in rows], "low": [lo for _, _, lo in rows],
         "close": [h for _, h, _ in rows], "open": [lo for _, _, lo in rows]},
        index=idx,
    )


ENTRY_DATE = "2026-09-10"


def _ex(rows, today=None):
    return compute_excursions(ENTRY, STOP, _bars(rows), ENTRY_DATE, today)


def test_favorable_and_adverse_are_measured_in_r() -> None:
    fav, adv = _ex([(ENTRY_DATE, 105, 99), ("2026-09-11", 130, 95)])
    assert fav == pytest.approx(3.0)    # 130 is +30 = 3R
    assert adv == pytest.approx(-0.5)   # 95 is -5 = -0.5R


def test_the_entry_bar_is_excluded() -> None:
    """Its high and low mostly printed before the 3 PM fill — same rule as
    `settle_one_trade`. Counting them would credit excursions we never had."""
    fav, adv = _ex([(ENTRY_DATE, 500, 10), ("2026-09-11", 101, 99)])
    assert fav == pytest.approx(0.1)
    assert adv == pytest.approx(-0.1)


def test_a_position_that_never_traded_up_has_no_favorable_excursion() -> None:
    """Not a negative one — 'max favorable -0.4R' would be nonsense."""
    fav, adv = _ex([(ENTRY_DATE, 100, 100), ("2026-09-11", 96, 94)])
    assert fav == 0.0
    assert adv == pytest.approx(-0.6)


def test_a_position_that_never_traded_down_has_no_adverse_excursion() -> None:
    fav, adv = _ex([(ENTRY_DATE, 100, 100), ("2026-09-11", 120, 101)])
    assert fav == pytest.approx(2.0)
    assert adv == 0.0


def test_bars_after_today_are_ignored() -> None:
    """So a re-run of the settle can't import the future."""
    rows = [(ENTRY_DATE, 100, 100), ("2026-09-11", 110, 99), ("2026-09-14", 200, 50)]
    fav, adv = _ex(rows, today=date(2026, 9, 11))
    assert fav == pytest.approx(1.0)
    assert adv == pytest.approx(-0.1)


def test_it_is_recomputed_not_accumulated() -> None:
    """Idempotent: settling twice over the same bars gives the same answer."""
    rows = [(ENTRY_DATE, 100, 100), ("2026-09-11", 130, 95)]
    assert _ex(rows) == _ex(rows)


def test_no_bars_after_entry_yet() -> None:
    assert compute_excursions(
        ENTRY, STOP, _bars([(ENTRY_DATE, 110, 90)]), ENTRY_DATE
    ) == (None, None)


def test_missing_inputs_return_none_rather_than_guessing() -> None:
    bars = _bars([(ENTRY_DATE, 110, 90), ("2026-09-11", 110, 90)])
    assert compute_excursions(None, STOP, bars, ENTRY_DATE) == (None, None)
    assert compute_excursions(ENTRY, None, bars, ENTRY_DATE) == (None, None)
    assert compute_excursions(ENTRY, STOP, bars, None) == (None, None)


def test_zero_risk_cannot_divide_by_zero() -> None:
    """An entry at the stop isn't a legitimate setup, but it must not crash."""
    bars = _bars([(ENTRY_DATE, 110, 90), ("2026-09-11", 110, 90)])
    assert compute_excursions(100.0, 100.0, bars, ENTRY_DATE) == (None, None)


def test_the_diagnostic_it_exists_for() -> None:
    """A trade that ran to +3.5R and was closed at the +2R target: the record
    now shows the target left money on the table."""
    fav, _ = _ex([(ENTRY_DATE, 100, 100), ("2026-09-11", 135, 100)])
    assert fav > 2.0
