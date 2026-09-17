"""Position-count and sector concentration limits.

`max_concurrent_positions: 8` sat in config, was parsed into `Config`, and had
no call site at all — so the scanner would open every breakout it confirmed.
There was no sector cap either, and since breakouts cluster by sector, eight
positions could be eight pharma names: one bet at 20% of capital dressed as
eight at 2.5%.
"""

from __future__ import annotations

from breakout.filters.concentration import (
    Candidate,
    select_within_limits,
    sector_of,
)

UNIVERSE = {
    "PHARMA1": {"symbol": "PHARMA1", "industry": "Pharmaceuticals"},
    "PHARMA2": {"symbol": "PHARMA2", "industry": "Pharmaceuticals"},
    "PHARMA3": {"symbol": "PHARMA3", "industry": "Healthcare Services"},
    "PHARMA4": {"symbol": "PHARMA4", "industry": "Pharmaceuticals"},
    "BANK1": {"symbol": "BANK1", "industry": "Banks"},
    "BANK2": {"symbol": "BANK2", "industry": "Banks"},
    "ODD1": {"symbol": "ODD1", "industry": "Miscellaneous Widgets"},
    "ODD2": {"symbol": "ODD2", "industry": "Assorted Sundries"},
}


def _cands(*pairs) -> list[Candidate]:
    return [Candidate(symbol=s, score=sc, payload={}) for s, sc in pairs]


def _open(*symbols) -> list[dict]:
    return [{"symbol": s} for s in symbols]


def _names(cands) -> list[str]:
    return [c.symbol for c in cands]


# ── The total position cap ───────────────────────────────────────────────


def test_the_total_cap_is_enforced_at_all() -> None:
    """It had no call site before this."""
    accepted, rejected = select_within_limits(
        _cands(("BANK1", 90), ("PHARMA1", 80)), _open("A", "B"), UNIVERSE,
        max_concurrent=3, max_per_sector=9,
    )
    assert len(accepted) == 1 and len(rejected) == 1


def test_existing_positions_consume_the_budget() -> None:
    """Limits have to hold across days, not reset every morning."""
    accepted, _ = select_within_limits(
        _cands(("BANK1", 90)), _open("A", "B", "C"), UNIVERSE,
        max_concurrent=3, max_per_sector=9,
    )
    assert accepted == []


def test_slots_go_to_the_highest_scoring_candidates() -> None:
    """Processing in watchlist order would hand capacity to whichever symbol
    happened to sort first — which is not a decision anyone made."""
    accepted, _ = select_within_limits(
        _cands(("ODD1", 61), ("BANK1", 95), ("ODD2", 70)), [], UNIVERSE,
        max_concurrent=2, max_per_sector=9,
    )
    assert _names(accepted) == ["BANK1", "ODD2"]


def test_selection_is_deterministic_on_ties() -> None:
    a, _ = select_within_limits(
        _cands(("ODD2", 70), ("ODD1", 70)), [], UNIVERSE, 1, 9
    )
    b, _ = select_within_limits(
        _cands(("ODD1", 70), ("ODD2", 70)), [], UNIVERSE, 1, 9
    )
    assert _names(a) == _names(b) == ["ODD1"]


# ── The sector cap ───────────────────────────────────────────────────────


def test_eight_positions_cannot_all_be_one_sector() -> None:
    """The headline case this exists for."""
    accepted, rejected = select_within_limits(
        _cands(("PHARMA1", 95), ("PHARMA2", 90), ("PHARMA3", 85), ("PHARMA4", 80)),
        [], UNIVERSE, max_concurrent=8, max_per_sector=2,
    )
    assert len(accepted) == 2
    assert all("sector_limit" in r.reason for r in rejected)


def test_the_sector_cap_counts_positions_already_held() -> None:
    accepted, rejected = select_within_limits(
        _cands(("PHARMA1", 95)), _open("PHARMA2", "PHARMA4"), UNIVERSE,
        max_concurrent=8, max_per_sector=2,
    )
    assert accepted == [] and "sector_limit" in rejected[0].reason


def test_a_full_sector_does_not_block_a_different_one() -> None:
    accepted, _ = select_within_limits(
        _cands(("PHARMA1", 95), ("BANK1", 60)), _open("PHARMA2", "PHARMA4"),
        UNIVERSE, max_concurrent=8, max_per_sector=2,
    )
    assert _names(accepted) == ["BANK1"]


def test_related_industries_share_a_sector_bucket() -> None:
    """'Healthcare Services' and 'Pharmaceuticals' are one bet, not two."""
    assert sector_of("PHARMA3", UNIVERSE) == sector_of("PHARMA1", UNIVERSE)


def test_unmapped_industries_do_not_share_a_bucket() -> None:
    """Two names we can't classify are unknown, not known-to-be-alike —
    lumping them under 'unknown' would make them compete for one sector's
    slots for no reason."""
    assert sector_of("ODD1", UNIVERSE) != sector_of("ODD2", UNIVERSE)
    accepted, _ = select_within_limits(
        _cands(("ODD1", 90), ("ODD2", 85)), [], UNIVERSE,
        max_concurrent=8, max_per_sector=1,
    )
    assert len(accepted) == 2


def test_a_symbol_missing_from_the_universe_does_not_crash() -> None:
    accepted, _ = select_within_limits(
        _cands(("GHOST", 90)), [], UNIVERSE, max_concurrent=8, max_per_sector=2
    )
    assert _names(accepted) == ["GHOST"]


# ── Disabling ────────────────────────────────────────────────────────────


def test_zero_means_no_limit() -> None:
    accepted, rejected = select_within_limits(
        _cands(("PHARMA1", 95), ("PHARMA2", 90), ("PHARMA4", 85)),
        _open("A", "B", "C"), UNIVERSE, max_concurrent=0, max_per_sector=0,
    )
    assert len(accepted) == 3 and rejected == []


def test_rejections_explain_which_limit_bound() -> None:
    """A dropped signal should never be silent — if this list is long and often,
    the limits or the capital are wrong."""
    _, rejected = select_within_limits(
        _cands(("PHARMA1", 95), ("PHARMA2", 90)), [], UNIVERSE,
        max_concurrent=1, max_per_sector=1,
    )
    assert len(rejected) == 1
    assert "position_limit" in rejected[0].reason or "sector_limit" in rejected[0].reason
