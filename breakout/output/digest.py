"""Paper-trade performance digest.

Summarises the closed paper trades into the metrics that tell you whether the
scanner has an edge: win rate, average R-multiple, profit factor, and average
holding period — overall and broken down by pattern.

`compute_digest` is pure (takes a list of trade dicts, returns stats) so it's
easy to test; `main` wires it to the store and prints a rich table.

Run with:  python -m breakout.output.digest
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PerfStats:
    """Performance over a set of settled trades (those with a realised P&L)."""

    n: int
    wins: int
    losses: int
    win_rate: float           # percent
    avg_r: float              # mean R-multiple, net of transaction costs
    profit_factor: float | None   # winners' P&L / losers' P&L; None if undefined
    avg_days_held: float
    # P&L in rupees. `net_inr` is what the account would show; `costs_inr` is
    # the round-trip transaction drag that separates it from `gross_inr`.
    # Reported rather than silently baked in, since on 3-5% moves the drag is
    # 5-10% of gross and decides whether marginal trades are worth taking.
    net_inr: float = 0.0
    gross_inr: float = 0.0
    costs_inr: float = 0.0


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # drop NaN


def _perf(trades: list[dict]) -> PerfStats:
    rs = [r for r in (_num(t.get("pnl_r")) for t in trades) if r is not None]
    n = len(rs)
    if n == 0:
        return PerfStats(0, 0, 0, 0.0, 0.0, None, 0.0)
    def _total(key: str) -> float:
        return sum(v for v in (_num(t.get(key)) for t in trades) if v is not None)
    wins = sum(1 for r in rs if r > 0)
    losses = n - wins
    avg_r = sum(rs) / n

    inrs = [v for v in (_num(t.get("pnl_inr")) for t in trades) if v is not None]
    gross_profit = sum(v for v in inrs if v > 0)
    gross_loss = -sum(v for v in inrs if v < 0)
    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")     # winners, no losers yet
    else:
        profit_factor = None

    days = [d for d in (_num(t.get("days_held")) for t in trades) if d is not None]
    avg_days = sum(days) / len(days) if days else 0.0

    # Trades settled before costs were modelled carry no `gross_pnl_inr`; fall
    # back to net so the total stays comparable rather than reading as zero.
    net_inr = _total("pnl_inr")
    costs_inr = _total("costs_inr")
    gross_inr = _total("gross_pnl_inr") or (net_inr + costs_inr)

    return PerfStats(
        n, wins, losses, wins / n * 100.0, avg_r, profit_factor, avg_days,
        net_inr=net_inr, gross_inr=gross_inr, costs_inr=costs_inr,
    )


def compute_digest(trades: list[dict]) -> dict:
    """Return {'overall': PerfStats, 'by_pattern': {pattern: PerfStats}}.

    Only *settled* trades (those carrying a realised `pnl_r`) count — open
    positions and never-entered CANCELED alerts are ignored.
    """
    settled = [t for t in trades if _num(t.get("pnl_r")) is not None]
    by_pattern: dict[str, list[dict]] = {}
    for t in settled:
        by_pattern.setdefault(t.get("pattern") or "unknown", []).append(t)
    return {
        "overall": _perf(settled),
        "by_pattern": {p: _perf(ts) for p, ts in sorted(by_pattern.items())},
    }


def _fmt_pf(pf: float | None) -> str:
    if pf is None:
        return "—"
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


def _fmt_drag(s: PerfStats) -> str:
    """Costs as a share of gross P&L — the number that says whether the edge
    survives execution. Undefined when gross P&L is zero or negative."""
    if s.gross_inr <= 0:
        return "—"
    return f"{s.costs_inr / s.gross_inr * 100.0:.0f}% of gross"


def render_digest(digest: dict) -> str:
    """Plain-text rendering (used for logs / tests)."""
    o: PerfStats = digest["overall"]
    lines = [
        "Paper-trade performance",
        f"  settled trades : {o.n}",
        f"  win rate       : {o.win_rate:.1f}%  ({o.wins}W / {o.losses}L)",
        f"  avg R          : {o.avg_r:+.2f}R  (net of costs)",
        f"  profit factor  : {_fmt_pf(o.profit_factor)}",
        f"  avg days held  : {o.avg_days_held:.1f}",
        f"  net P&L        : ₹{o.net_inr:+,.0f}",
        f"  gross P&L      : ₹{o.gross_inr:+,.0f}   "
        f"costs ₹{o.costs_inr:,.0f} ({_fmt_drag(o)})",
    ]
    if digest["by_pattern"]:
        lines.append("  by pattern:")
        for pat, s in digest["by_pattern"].items():
            lines.append(
                f"    {pat:<20} n={s.n:<3} win={s.win_rate:4.0f}%  "
                f"avgR={s.avg_r:+.2f}  pf={_fmt_pf(s.profit_factor)}"
            )
    return "\n".join(lines)


def main() -> None:
    from breakout.config import ensure_runtime_dirs, load_config
    from breakout.data.store import Store
    from breakout.logging_setup import setup_logging

    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    with Store(cfg.paths.workbook) as store:
        trades = store.read_paper_trades_by_state()
    digest = compute_digest(trades)
    print(render_digest(digest))


if __name__ == "__main__":
    main()
