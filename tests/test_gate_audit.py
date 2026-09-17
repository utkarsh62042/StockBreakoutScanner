"""The quality-gate audit.

Three of the hard filters silently passed every symbol while reading as active
in config and in the GUIDE. A gate that quietly approves everything is worse
than no gate: it reads as protection you do not have. This report makes that
state visible on every run, so a filter that stops working surfaces the next
morning rather than at the next design review.
"""

from __future__ import annotations

from types import SimpleNamespace

from breakout.filters.gate_audit import (
    ACTIVE,
    INERT,
    REDUNDANT,
    audit_gates,
    render_audit,
)

THRESHOLDS = SimpleNamespace(min_market_cap_cr=500.0, min_adv_cr=5.0)

BARE = [{"symbol": "A"}, {"symbol": "B"}]           # what NSE's CSV gives us
RICH = [
    {"symbol": "A", "market_cap_cr": 900.0, "promoter_pledge_pct": 5.0,
     "listing_date": "2015-01-01"},
]


def _by_name(universe, **kw) -> dict:
    return {s.name: s for s in audit_gates(universe, THRESHOLDS, **kw)}


# ── The three dead gates ─────────────────────────────────────────────────


def test_promoter_pledge_is_reported_inert() -> None:
    """The one that matters: it would reject real names if wired."""
    s = _by_name(BARE)["promoter_pledge"]
    assert s.state == INERT
    assert "no source" in s.detail


def test_market_cap_is_redundant_not_inert() -> None:
    """Distinguished deliberately — index membership already implies a size
    floor ~20x this one, so there is nothing to fix and no source to wire."""
    s = _by_name(BARE)["market_cap"]
    assert s.state == REDUNDANT
    assert "NIFTY 500" in s.detail


def test_earnings_is_inert_when_no_symbol_returns_a_date() -> None:
    s = _by_name(BARE, earnings_symbols_checked=120, earnings_symbols_with_dates=0)[
        "earnings_blackout"
    ]
    assert s.state == INERT
    assert "0/120" in s.detail


def test_earnings_becomes_active_once_a_source_provides_dates() -> None:
    """So wiring an Indian source flips this without touching the audit."""
    s = _by_name(BARE, earnings_symbols_checked=120, earnings_symbols_with_dates=90)[
        "earnings_blackout"
    ]
    assert s.state == ACTIVE


# ── The gate that does work ──────────────────────────────────────────────


def test_adv_reports_what_it_actually_rejected() -> None:
    s = _by_name(BARE, adv_rejected=4, symbols_checked=500)["adv"]
    assert s.state == ACTIVE
    assert "4/500" in s.detail and "0.8%" in s.detail


def test_empty_excel_cells_do_not_read_as_present() -> None:
    """Regression. The workbook is Excel-backed, so an empty cell in a numeric
    column comes back as NaN, not None. Testing `is not None` made the audit
    report "501/501 symbols carry a market cap" when not one of them did —
    exactly the silent falseness this module exists to catch."""
    nan_universe = [
        {"symbol": "A", "market_cap_cr": float("nan"),
         "promoter_pledge_pct": float("nan"), "listing_date": float("nan")},
    ] * 501
    statuses = _by_name(nan_universe)
    assert statuses["market_cap"].state == REDUNDANT
    assert statuses["promoter_pledge"].state == INERT
    assert statuses["listing_age"].state == REDUNDANT
    assert "501/501" not in render_audit(audit_gates(nan_universe, THRESHOLDS))


def test_blank_strings_do_not_read_as_present() -> None:
    assert _by_name([{"symbol": "A", "listing_date": "  "}])["listing_age"].state == (
        REDUNDANT
    )


def test_gates_go_active_when_the_metadata_arrives() -> None:
    statuses = _by_name(RICH)
    assert statuses["market_cap"].state == ACTIVE
    assert statuses["promoter_pledge"].state == ACTIVE
    assert statuses["listing_age"].state == ACTIVE


# ── The rendered report ──────────────────────────────────────────────────


def test_the_report_names_every_gate() -> None:
    text = render_audit(audit_gates(BARE, THRESHOLDS))
    for gate in ("adv", "market_cap", "listing_age", "promoter_pledge",
                 "earnings_blackout"):
        assert gate in text


def test_the_report_calls_out_inert_gates_explicitly() -> None:
    """Burying it in a table would let it be skimmed past."""
    text = render_audit(
        audit_gates(BARE, THRESHOLDS, earnings_symbols_checked=10,
                    earnings_symbols_with_dates=0)
    )
    assert "INERT" in text
    assert "promoter_pledge" in text and "earnings_blackout" in text
    assert "treat the protection they imply as absent" in text


def test_a_fully_wired_setup_reports_no_inert_gates() -> None:
    text = render_audit(
        audit_gates(RICH, THRESHOLDS, earnings_symbols_checked=5,
                    earnings_symbols_with_dates=5)
    )
    assert "INERT" not in text


def test_audit_never_blocks_anything() -> None:
    """It is a report. It returns statuses and nothing else."""
    statuses = audit_gates(BARE, THRESHOLDS)
    assert all(s.state in {ACTIVE, INERT, REDUNDANT} for s in statuses)
    assert len(statuses) == 5
