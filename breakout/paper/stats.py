"""Detailed paper-trade analytics.

Extends the headline digest with breakdowns by pattern, score band, and
sector — the cuts you actually use to tune the scanner ("which patterns work",
"is the 60-70 band worth alerting", "do flat-sector breakouts underperform").

Reuses `output.digest` for the per-group performance math so the two stay
consistent. `full_report` is pure; `main` wires it to the store + a `--period`
filter.

Run with:  python -m breakout.paper.stats --period 30d
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from breakout.output.digest import PerfStats, _num, _perf, compute_digest, render_digest

logger = logging.getLogger(__name__)


# Score bands (lower inclusive, upper exclusive); the last one catches 90-100.
DEFAULT_BANDS: list[tuple[int, int]] = [(60, 70), (70, 80), (80, 90), (90, 101)]


def _settled(trades: list[dict]) -> list[dict]:
    return [t for t in trades if _num(t.get("pnl_r")) is not None]


def by_score_band(trades: list[dict], bands: list[tuple[int, int]] = DEFAULT_BANDS) -> dict[str, PerfStats]:
    settled = _settled(trades)
    out: dict[str, PerfStats] = {}
    for lo, hi in bands:
        grp = [t for t in settled if (_num(t.get("score")) is not None and lo <= _num(t.get("score")) < hi)]
        label = f"{lo}-{min(hi - 1, 100)}"
        out[label] = _perf(grp)
    return out


def by_sector(trades: list[dict], sector_of: dict[str, str]) -> dict[str, PerfStats]:
    settled = _settled(trades)
    groups: dict[str, list[dict]] = {}
    for t in settled:
        sec = sector_of.get(t.get("symbol"), "unknown") or "unknown"
        groups.setdefault(sec, []).append(t)
    return {s: _perf(g) for s, g in sorted(groups.items())}


def filter_period(trades: list[dict], period: str | None) -> list[dict]:
    """Keep trades whose exit_date is within the last `period` (e.g. '30d').

    None or 'all' returns everything. Trades with no exit_date are dropped when
    a period is set (they haven't closed in-window)."""
    if not period or period == "all":
        return trades
    days = int(str(period).rstrip("dD"))
    cutoff = date.today() - timedelta(days=days)
    out = []
    for t in trades:
        ed = t.get("exit_date")
        if not ed:
            continue
        try:
            d = date.fromisoformat(str(ed)[:10])
        except ValueError:
            continue
        if d >= cutoff:
            out.append(t)
    return out


def full_report(trades: list[dict], sector_of: dict[str, str] | None = None) -> dict:
    d = compute_digest(trades)
    return {
        "overall": d["overall"],
        "by_pattern": d["by_pattern"],
        "by_score_band": by_score_band(trades),
        "by_sector": by_sector(trades, sector_of or {}),
    }


def _fmt_group(title: str, groups: dict[str, PerfStats]) -> list[str]:
    lines = [f"  by {title}:"]
    for k, s in groups.items():
        if s.n == 0:
            continue
        pf = "∞" if s.profit_factor == float("inf") else ("—" if s.profit_factor is None else f"{s.profit_factor:.2f}")
        lines.append(f"    {k:<16} n={s.n:<3} win={s.win_rate:4.0f}%  avgR={s.avg_r:+.2f}  pf={pf}")
    return lines


def render_report(report: dict) -> str:
    lines = [render_digest({"overall": report["overall"], "by_pattern": report["by_pattern"]})]
    lines += _fmt_group("score band", report["by_score_band"])
    lines += _fmt_group("sector", report["by_sector"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-trade analytics")
    parser.add_argument("--period", default="all", help="e.g. 30d, 90d, all")
    args = parser.parse_args()

    from breakout.config import ensure_runtime_dirs, load_config
    from breakout.data.store import Store
    from breakout.filters.mood import classify_sector
    from breakout.logging_setup import setup_logging

    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)
    with Store(cfg.paths.workbook) as store:
        trades = filter_period(store.read_paper_trades_by_state(), args.period)
        sector_of = {u["symbol"]: (classify_sector(u.get("industry")) or "unknown") for u in store.read_universe()}
    print(render_report(full_report(trades, sector_of)))


if __name__ == "__main__":
    main()
