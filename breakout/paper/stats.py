"""Detailed paper-trade analytics.

Extends the headline digest with breakdowns by pattern, score band, and
sector — the cuts you actually use to tune the scanner ("which patterns work",
"is the 60-70 band worth alerting", "do flat-sector breakouts underperform").

Plus the cut that lets the scoring model improve: `feature_report` buckets every
signal recorded in `alert_features` against the realised outcome, so the weights
in `scoring.py` can eventually be set from results instead of priors. Until
there are a few dozen settled trades per bucket, read it as noise — buckets
under `MIN_MEANINGFUL_N` are marked "(thin)".

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


# ── Feature-vs-outcome analysis ──────────────────────────────────────────
# The question the composite score cannot answer about itself: does each signal
# it weights actually predict anything? `alert_features` rows carry every
# predictor the scanner saw; joined to the realised `pnl_r`, a per-bucket win
# rate and average R say which weights are earning their place. Read these as
# evidence to re-weight `scoring.py`, not as a result — you need a few dozen
# settled trades per bucket before a difference means anything.

#: Numeric features worth bucketing, and the feature's own column name. Each is
#: split into terciles by observed value rather than fixed bands, because the
#: useful cut points aren't known in advance and depend on the regime.
NUMERIC_FEATURES = (
    "pattern_confidence", "rs_percentile", "tightness", "volume_ratio",
    "distance_pct", "extension_pct", "close_in_range", "atr_pct", "vix",
    "breadth_pct", "regime_score",
)
#: Categorical features — grouped by value.
CATEGORICAL_FEATURES = (
    "sector_trend", "market_mood", "stage", "alert_type", "regime",
    "nifty_trend",
)

#: Buckets below this size are reported but flagged, so a 2-trade "80% win
#: rate" can't be mistaken for a finding.
MIN_MEANINGFUL_N = 10


def _terciles(values: list[float]) -> tuple[float, float] | None:
    """Lower and upper tercile cut points, or None if too few distinct values."""
    vals = sorted(values)
    if len(vals) < 6 or vals[0] == vals[-1]:
        return None
    lo = vals[len(vals) // 3]
    hi = vals[2 * len(vals) // 3]
    if lo == hi:
        return None
    return lo, hi


def by_numeric_feature(rows: list[dict], feature: str) -> dict[str, PerfStats]:
    """Tercile split of `feature` over settled alerts."""
    settled = [r for r in _settled(rows) if _num(r.get(feature)) is not None]
    if not settled:
        return {}
    cuts = _terciles([_num(r.get(feature)) for r in settled])
    if cuts is None:
        return {f"{feature}=all": _perf(settled)}
    lo, hi = cuts
    groups: dict[str, list[dict]] = {
        f"low (<{lo:.2f})": [],
        f"mid ({lo:.2f}-{hi:.2f})": [],
        f"high (>={hi:.2f})": [],
    }
    keys = list(groups)
    for r in settled:
        v = _num(r.get(feature))
        groups[keys[0] if v < lo else (keys[2] if v >= hi else keys[1])].append(r)
    return {k: _perf(g) for k, g in groups.items()}


def by_categorical_feature(rows: list[dict], feature: str) -> dict[str, PerfStats]:
    groups: dict[str, list[dict]] = {}
    for r in _settled(rows):
        groups.setdefault(str(r.get(feature) or "unknown"), []).append(r)
    return {k: _perf(g) for k, g in sorted(groups.items())}


def feature_report(rows: list[dict]) -> dict[str, dict[str, PerfStats]]:
    """Every feature's buckets, keyed by feature name. Empty ones are dropped."""
    out: dict[str, dict[str, PerfStats]] = {}
    for f in NUMERIC_FEATURES:
        groups = by_numeric_feature(rows, f)
        if groups:
            out[f] = groups
    for f in CATEGORICAL_FEATURES:
        groups = by_categorical_feature(rows, f)
        if len(groups) > 1:      # a single value tells you nothing
            out[f] = groups
    return out


def full_report(
    trades: list[dict],
    sector_of: dict[str, str] | None = None,
    alert_rows: list[dict] | None = None,
) -> dict:
    d = compute_digest(trades)
    return {
        "overall": d["overall"],
        "by_pattern": d["by_pattern"],
        "by_score_band": by_score_band(trades),
        "by_sector": by_sector(trades, sector_of or {}),
        "by_feature": feature_report(alert_rows or []),
    }


def _fmt_group(title: str, groups: dict[str, PerfStats], width: int = 16) -> list[str]:
    lines = [f"  by {title}:"]
    for k, s in groups.items():
        if s.n == 0:
            continue
        pf = "∞" if s.profit_factor == float("inf") else ("—" if s.profit_factor is None else f"{s.profit_factor:.2f}")
        thin = "  (thin)" if s.n < MIN_MEANINGFUL_N else ""
        lines.append(
            f"    {k:<{width}} n={s.n:<3} win={s.win_rate:4.0f}%  "
            f"avgR={s.avg_r:+.2f}  pf={pf}{thin}"
        )
    return lines


def render_report(report: dict) -> str:
    lines = [render_digest({"overall": report["overall"], "by_pattern": report["by_pattern"]})]
    lines += _fmt_group("score band", report["by_score_band"])
    lines += _fmt_group("sector", report["by_sector"])
    features = report.get("by_feature") or {}
    if features:
        lines.append("")
        lines.append("Signal vs outcome  (does each weighted feature predict anything?)")
        for name, groups in features.items():
            lines += _fmt_group(name, groups, width=22)
    else:
        lines.append("")
        lines.append(
            "Signal vs outcome: no settled alerts with features yet — the "
            "alert_features sheet fills from the next pre-close scan onward."
        )
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
        alert_rows = filter_period(store.read_alerts_with_outcomes(), args.period)
    print(render_report(full_report(trades, sector_of, alert_rows)))


if __name__ == "__main__":
    main()
