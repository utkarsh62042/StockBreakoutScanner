"""Position sizing must respect the size of the account.

Risk-based sizing knows what a position can *lose*, not what it *costs*, and the
two diverge violently when the stop is tight:

    position value = risk_amount / (stop distance as a fraction of price)

so a 2.5% stop with 2.5% capital risk buys exactly 100% of the account in one
trade, and anything tighter buys more than the account holds.

Found by running the backtest over real cached prices (2026-09-12): across 94
setups the median position was **85% of capital**, 30 of 94 exceeded 100%, and
the largest was ₹6,84,607 against a ₹2,00,000 account — a GRASIM entry at
₹3,140 with the stop ₹23 below it. Every one of the 94 exceeded 25% of capital.
These were not share counts the account could have bought, and the same function
sizes live paper trades and the figure printed in alerts.
"""

from __future__ import annotations

import pytest

from breakout.paper.tracker import max_position_value, position_size

CAPITAL = 200_000.0
RISK_PCT = 2.5
SLOTS = 8
CAP = max_position_value(CAPITAL, SLOTS)      # 25,000


def _value(entry: float, stop: float) -> float:
    return position_size(CAPITAL, RISK_PCT, entry, stop, max_value=CAP) * entry


# ── The cap itself ───────────────────────────────────────────────────────


def test_a_full_book_fits_the_account() -> None:
    """The cap is derived from max_concurrent_positions precisely so that
    holding the maximum number of positions is exactly 100% of capital."""
    assert CAP * SLOTS == pytest.approx(CAPITAL)


def test_the_cap_is_never_exceeded_however_tight_the_stop() -> None:
    for entry, stop in [(3140.0, 3117.0), (8898.0, 8823.0), (1000.0, 977.5),
                        (150.0, 148.0), (500.0, 499.0)]:
        assert _value(entry, stop) <= CAP


def test_the_grasim_case() -> None:
    """The worst real offender: 218 shares at ₹3,140 = ₹6,84,607, 342% of a
    ₹2,00,000 account."""
    uncapped = position_size(CAPITAL, RISK_PCT, 3140.4, 3117.5)
    assert uncapped * 3140.4 > 3 * CAPITAL          # what it used to do
    assert _value(3140.4, 3117.5) <= CAP


def test_a_wide_stop_is_unaffected_by_the_cap() -> None:
    """The cap must only bind where risk sizing overshoots — a genuinely wide
    stop already produces a small position and should be left alone."""
    entry, stop = 220.0, 204.0                      # 7.3% stop
    risk_only = position_size(CAPITAL, RISK_PCT, entry, stop)
    capped = position_size(CAPITAL, RISK_PCT, entry, stop, max_value=CAP)
    assert risk_only * entry > CAP                   # this one does bind
    wide_entry, wide_stop = 220.0, 110.0             # 50% stop
    assert position_size(CAPITAL, RISK_PCT, wide_entry, wide_stop) == position_size(
        CAPITAL, RISK_PCT, wide_entry, wide_stop, max_value=CAP
    )
    assert capped <= risk_only


def test_risk_sizing_still_applies_below_the_cap() -> None:
    """A wider stop must still buy fewer shares — the cap is a ceiling, not a
    replacement for risk-based sizing."""
    tight = position_size(CAPITAL, RISK_PCT, 220.0, 210.0, max_value=CAP)
    wide = position_size(CAPITAL, RISK_PCT, 220.0, 110.0, max_value=CAP)
    assert wide < tight


def test_no_cap_preserves_the_old_behaviour() -> None:
    """Omitting max_value is still the pure risk calculation, so the helper can
    be reasoned about on its own."""
    assert position_size(CAPITAL, RISK_PCT, 1000.0, 977.5) == 222


def test_a_share_that_costs_more_than_the_cap_yields_zero() -> None:
    """Better to take nothing than to take a position the account cannot pay
    for — MRF-priced shares against a small cap."""
    assert position_size(CAPITAL, RISK_PCT, 150_000.0, 149_000.0, max_value=CAP) == 0


def test_zero_risk_still_returns_zero() -> None:
    assert position_size(CAPITAL, RISK_PCT, 220.0, 220.0, max_value=CAP) == 0


def test_max_position_value_survives_a_zero_slot_count() -> None:
    assert max_position_value(CAPITAL, 0) == pytest.approx(CAPITAL)
