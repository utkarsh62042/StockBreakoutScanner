"""Which hard gates are actually doing anything?

Three of the quality filters silently did nothing, and nothing in the system
said so — they read as active in `config.yaml`, they appear in the GUIDE, and
they default-pass every symbol. A filter that quietly passes everything is worse
than no filter: it reads as protection you do not have.

This module makes that visible. It is a *report*, not a gate — it never blocks a
symbol. The morning scan logs it once per run, so a gate that stops working
(a source goes away, a column empties) shows up the next morning instead of at
the next design review.

Three states:

    ACTIVE     the gate can and does reject symbols
    INERT      the gate is configured but has no data to act on, so it
               default-passes everything — a missing source, not a design choice
    REDUNDANT  the gate works, but something upstream already guarantees it;
               it will never be the reason a symbol is rejected

REDUNDANT is not a bug. It is worth distinguishing from ACTIVE so nobody spends
effort "fixing" a threshold that cannot bind.
"""

from __future__ import annotations

from dataclasses import dataclass


ACTIVE = "ACTIVE"
INERT = "INERT"
REDUNDANT = "REDUNDANT"


def _has_value(v) -> bool:
    """True only for a real, usable value.

    `is not None` is not enough: the workbook is Excel-backed, so an empty cell
    in a numeric column reads back as `float('nan')`, not `None`. Testing for
    None alone made this module report a gate ACTIVE with "501/501 symbols carry
    a market cap" when not one of them did — precisely the silent falseness it
    exists to catch.
    """
    if v is None:
        return False
    if isinstance(v, float) and v != v:      # NaN
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


@dataclass(frozen=True)
class GateStatus:
    name: str
    state: str
    detail: str

    def line(self) -> str:
        return f"{self.state:<9} {self.name:<18} {self.detail}"


def _market_cap(universe: list[dict], thresholds) -> GateStatus:
    """Market-cap floor.

    Measured 2026-09-12: the 15 lowest-turnover NIFTY 500 names have market caps
    from ₹10,052 cr up — twenty times the ₹500 cr floor. Index membership is
    itself a far stricter size filter than this threshold, so even with a source
    wired the gate could not reject anything. Not worth building.
    """
    known = sum(1 for m in universe if _has_value(m.get("market_cap_cr")))
    if known == 0:
        return GateStatus(
            "market_cap",
            REDUNDANT,
            f"no source (NSE's CSV has no such column), but NIFTY 500 membership "
            f"already implies a market cap far above the "
            f"{thresholds.min_market_cap_cr:.0f}cr floor — smallest measured "
            f"constituent was ~10,000cr. Nothing to fix.",
        )
    return GateStatus(
        "market_cap", ACTIVE, f"{known}/{len(universe)} symbols carry a market cap"
    )


def _promoter_pledge(universe: list[dict]) -> GateStatus:
    """Promoter-pledge ceiling — genuinely unenforced.

    Unlike market cap this one *would* reject real symbols if wired: a heavily
    pledged promoter is a genuine risk that index membership does not screen
    out. There is no free, reliable source (NSE publishes shareholding patterns
    quarterly as filings, not as an API), so it stays inert and honest.
    """
    known = sum(1 for m in universe if _has_value(m.get("promoter_pledge_pct")))
    if known == 0:
        return GateStatus(
            "promoter_pledge",
            INERT,
            "no source wired — passes every symbol. This one would reject real "
            "names if connected; index membership does not screen for pledging.",
        )
    return GateStatus(
        "promoter_pledge", ACTIVE, f"{known}/{len(universe)} symbols carry a pledge %"
    )


def _earnings(symbols_checked: int, symbols_with_dates: int) -> GateStatus:
    """Earnings blackout — inert on yfinance, which has no NSE earnings coverage."""
    if symbols_checked == 0:
        return GateStatus("earnings_blackout", INERT, "not exercised this run")
    if symbols_with_dates == 0:
        return GateStatus(
            "earnings_blackout",
            INERT,
            f"0/{symbols_checked} symbols returned any earnings date — the feed "
            f"has no NSE coverage, so the blackout never fires. Needs an Indian "
            f"source (NSE corporate announcements / a scraped calendar).",
        )
    return GateStatus(
        "earnings_blackout",
        ACTIVE,
        f"{symbols_with_dates}/{symbols_checked} symbols had earnings dates",
    )


def _adv(rejected: int, checked: int, thresholds) -> GateStatus:
    """Average daily turnover.

    Measured 2026-09-12 over the cached universe: median ADV ₹83 cr, minimum
    ₹3.3 cr, and the ₹5 cr floor rejects 4 of 500 names. At ₹2L of capital a
    position is ₹5-15k, so even the thinnest constituent trades ~250x a
    position's size daily — this is not a binding liquidity constraint. It is
    kept as a cheap safety rail against a genuinely thin name entering the
    index, not because fills are at risk.
    """
    if checked == 0:
        return GateStatus("adv", ACTIVE, "not exercised this run")
    pct = rejected / checked * 100.0
    return GateStatus(
        "adv",
        ACTIVE,
        f"rejected {rejected}/{checked} ({pct:.1f}%) below "
        f"{thresholds.min_adv_cr}cr — a safety rail, not a real constraint at "
        f"this capital",
    )


def _listing_age(universe: list[dict]) -> GateStatus:
    known = sum(1 for m in universe if _has_value(m.get("listing_date")))
    if known == 0:
        return GateStatus(
            "listing_age",
            REDUNDANT,
            "no listing_date on universe rows; in practice the 200-bar history "
            "requirement already excludes anything recently listed.",
        )
    return GateStatus("listing_age", ACTIVE, f"{known}/{len(universe)} carry a date")


def audit_gates(
    universe: list[dict],
    thresholds,
    *,
    adv_rejected: int = 0,
    symbols_checked: int = 0,
    earnings_symbols_checked: int = 0,
    earnings_symbols_with_dates: int = 0,
) -> list[GateStatus]:
    """Report the state of every hard gate. Never blocks anything."""
    return [
        _adv(adv_rejected, symbols_checked, thresholds),
        _market_cap(universe, thresholds),
        _listing_age(universe),
        _promoter_pledge(universe),
        _earnings(earnings_symbols_checked, earnings_symbols_with_dates),
    ]


def render_audit(statuses: list[GateStatus]) -> str:
    inert = [s for s in statuses if s.state == INERT]
    lines = ["quality-gate audit:"]
    lines += [f"  {s.line()}" for s in statuses]
    if inert:
        lines.append(
            f"  -> {len(inert)} gate(s) INERT: "
            f"{', '.join(s.name for s in inert)}. These pass every symbol; "
            f"treat the protection they imply as absent."
        )
    return "\n".join(lines)
