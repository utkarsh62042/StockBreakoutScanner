"""Position-count and sector concentration limits.

`max_concurrent_positions: 8` sat in `config.yaml`, was parsed into `Config`,
and was read by nothing at all — there was no call site. So the scanner would
happily open every confirmed breakout it found, and 2.5% risk per trade across
fifteen simultaneous positions is not 2.5% risk.

Worse, there was no sector cap either. Breakouts cluster: sector moves are the
single most common reason a batch of setups all confirm on the same day, so
"eight positions" can easily mean eight pharma names. That is one bet at 20% of
capital wearing the costume of eight bets at 2.5% — and for a breakout strategy
it is the most common way a good signal becomes a bad month.

Two limits, both applied at entry:

    max_concurrent_positions   total open positions
    max_positions_per_sector   open positions sharing a sector

When more candidates confirm than there are slots, the slots go to the
**highest-scoring** candidates. That matters: processing in watchlist order
would hand the day's capacity to whichever symbol happened to sort first.

Sector comes from the universe metadata via `mood.classify_sector`, the same
mapping the scoring uses. Symbols whose industry doesn't map to a canonical
sector are treated as their own single-name bucket rather than being lumped
together under "unknown" — otherwise every unmapped name would compete for one
sector's slots for no reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from breakout.filters.mood import classify_sector


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """A confirmed breakout awaiting a slot."""

    symbol: str
    score: float
    payload: dict          # whatever the caller needs to actually open it


@dataclass(frozen=True)
class Rejection:
    symbol: str
    reason: str


def sector_of(symbol: str, universe_by_symbol: dict[str, dict]) -> str:
    """Canonical sector for a symbol, or a unique per-symbol bucket.

    An unmapped industry returns `"?<symbol>"` so unmapped names never share a
    bucket — they are unknown, not known-to-be-alike.
    """
    meta = universe_by_symbol.get(symbol) or {}
    return classify_sector(meta.get("industry")) or f"?{symbol}"


def select_within_limits(
    candidates: list[Candidate],
    open_trades: list[dict],
    universe_by_symbol: dict[str, dict],
    max_concurrent: int,
    max_per_sector: int,
) -> tuple[list[Candidate], list[Rejection]]:
    """Pick which candidates get opened. Returns `(accepted, rejected)`.

    Highest score first. Already-open positions consume both the total and the
    per-sector budget, so limits hold across days rather than resetting each
    morning.
    """
    open_by_sector: dict[str, int] = {}
    for t in open_trades:
        sec = sector_of(str(t.get("symbol") or ""), universe_by_symbol)
        open_by_sector[sec] = open_by_sector.get(sec, 0) + 1
    total_open = len(open_trades)

    accepted: list[Candidate] = []
    rejected: list[Rejection] = []

    # Ties broken by symbol so the outcome is deterministic across runs.
    for cand in sorted(candidates, key=lambda c: (-c.score, c.symbol)):
        if max_concurrent > 0 and total_open >= max_concurrent:
            rejected.append(
                Rejection(
                    cand.symbol,
                    f"position_limit:{total_open}/{max_concurrent} already open",
                )
            )
            continue
        sec = sector_of(cand.symbol, universe_by_symbol)
        in_sector = open_by_sector.get(sec, 0)
        if max_per_sector > 0 and in_sector >= max_per_sector:
            rejected.append(
                Rejection(
                    cand.symbol,
                    f"sector_limit:{sec} already has {in_sector}/{max_per_sector}",
                )
            )
            continue
        accepted.append(cand)
        total_open += 1
        open_by_sector[sec] = in_sector + 1

    return accepted, rejected


def log_rejections(rejected: list[Rejection]) -> None:
    """Say plainly which signals were dropped for capacity rather than quality.

    A setup skipped because the book is full is not a setup that failed — if
    this list is long and often, the limits or the capital are wrong, and that
    is worth seeing rather than inferring from a quiet log.
    """
    if not rejected:
        return
    logger.warning(
        f"{len(rejected)} confirmed breakout(s) not taken — at concentration "
        f"limits, not for lack of quality:"
    )
    for r in rejected:
        logger.warning(f"  {r.symbol}: {r.reason}")
