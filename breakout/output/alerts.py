"""Alert formatting and dispatch.

The dispatcher is pluggable: each enabled channel (CSV, email) is
called for every alert. Phase 1 ships only the CSV channel; the
email channel is scaffolded so flipping it on later is a one-line
config change plus credential setup.
"""

from __future__ import annotations

import csv
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from breakout.config import Config


logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """One row of the alert output, ready to render to any channel."""

    symbol: str
    score: float
    pattern: str
    breakout_level: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    position_size: int
    volume_ratio: float
    stage: str
    rs_rank: float = 0.0
    tightness: float = 0.0
    sector: str = "flat"
    notes: str = ""
    alert_type: str = "BREAKOUT"  # or PULLBACK_ENTRY

    def to_row(self) -> dict[str, Any]:
        """Flat dict ready for CSV row writing."""
        return asdict(self)


# ── Channel interface ────────────────────────────────────────────────────


class AlertChannel(ABC):
    """A single delivery channel for alerts (CSV, Telegram, email, ...)."""

    @abstractmethod
    def emit(self, alerts: list[Alert], run_date: date) -> None:
        """Deliver the batch of alerts. Implementations should NOT raise on
        individual failures — log and continue so one broken channel can't
        block the others."""


# ── CSV channel ──────────────────────────────────────────────────────────


_CSV_FIELDS = [
    "alert_type",
    "symbol",
    "score",
    "pattern",
    "breakout_level",
    "entry_price",
    "stop_loss",
    "target_1",
    "target_2",
    "position_size",
    "volume_ratio",
    "stage",
    "rs_rank",
    "tightness",
    "sector",
    "notes",
]


class CSVChannel(AlertChannel):
    """Writes one CSV per trading day to `output/alerts_YYYY-MM-DD.csv`.

    If the file already exists (e.g. morning scan ran earlier), new rows
    are appended without rewriting the header.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, alerts: list[Alert], run_date: date) -> None:
        path = self.output_dir / f"alerts_{run_date.isoformat()}.csv"
        new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            if new_file:
                writer.writeheader()
            for alert in alerts:
                row = alert.to_row()
                # Round numeric fields for readability
                for k in ("score", "breakout_level", "entry_price", "stop_loss",
                          "target_1", "target_2", "volume_ratio", "rs_rank",
                          "tightness"):
                    if isinstance(row.get(k), float):
                        row[k] = round(row[k], 2)
                writer.writerow({k: row.get(k, "") for k in _CSV_FIELDS})
        logger.info(f"wrote {len(alerts)} alerts to {path}")


# ── Console preview ──────────────────────────────────────────────────────


def render_alerts_table(alerts: list[Alert], title: str = "Alerts") -> None:
    """Pretty-print alerts to the console using `rich`."""
    if not alerts:
        Console().print(f"[yellow]{title}: no alerts[/yellow]")
        return
    table = Table(title=title, show_lines=False)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Score", justify="right")
    table.add_column("Pattern")
    table.add_column("Entry", justify="right")
    table.add_column("Stop", justify="right")
    table.add_column("T1", justify="right")
    table.add_column("T2", justify="right")
    table.add_column("Shares", justify="right")
    table.add_column("Vol×", justify="right")
    table.add_column("Type")
    for a in alerts:
        table.add_row(
            a.symbol,
            f"{a.score:.0f}",
            a.pattern,
            f"{a.entry_price:.2f}",
            f"{a.stop_loss:.2f}",
            f"{a.target_1:.2f}",
            f"{a.target_2:.2f}",
            str(a.position_size),
            f"{a.volume_ratio:.1f}",
            a.alert_type,
        )
    Console().print(table)


# ── Dispatcher ───────────────────────────────────────────────────────────


def build_channels(cfg: Config) -> list[AlertChannel]:
    """Construct the active channels from config. CSV is always on
    when `output.csv` is true."""
    channels: list[AlertChannel] = []
    if cfg.output.csv:
        channels.append(CSVChannel(cfg.paths.output))
    return channels


def dispatch_alerts(
    alerts: list[Alert],
    channels: list[AlertChannel],
    run_date: date | None = None,
) -> None:
    """Send the alert batch to every active channel."""
    run_date = run_date or date.today()
    for ch in channels:
        try:
            ch.emit(alerts, run_date)
        except Exception as e:
            logger.exception(f"channel {ch.__class__.__name__} failed: {e}")
