"""Alert formatting and dispatch.

The dispatcher is pluggable: each enabled channel (CSV, email) is
called for every alert. Phase 1 ships only the CSV channel; the
email channel is scaffolded so flipping it on later is a one-line
config change plus credential setup.
"""

from __future__ import annotations

import csv
import requests
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


# ── Telegram channel ────────────────────────────────────────────────

class TelegramChannel(AlertChannel):
    """Send breakout alerts to the configured Telegram chat."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def emit(self, alerts: list[Alert], run_date: date) -> None:
        if not alerts:
            logger.info("Telegram: no breakout alerts to send")
            return

        lines = [
            f"Stock Breakout Scanner — {run_date.isoformat()}",
            f"Alerts: {len(alerts)}",
            "",
        ]

        for a in alerts:
            lines.extend([
                f"{a.symbol} — {a.alert_type}",
                f"Pattern: {a.pattern} | Score: {a.score:.0f}",
                f"Entry: {a.entry_price:.2f}",
                f"Stop: {a.stop_loss:.2f}",
                f"Target 1: {a.target_1:.2f} | Target 2: {a.target_2:.2f}",
                f"Volume ratio: {a.volume_ratio:.1f}x",
                "",
            ])

        # Telegram allows up to 4096 characters per message.
        # Split on alert boundaries to stay comfortably below the limit.
        chunks = []
        current = lines[:3]

        for a in alerts:
            block = [
                f"{a.symbol} — {a.alert_type}",
                f"Pattern: {a.pattern} | Score: {a.score:.0f}",
                f"Entry: {a.entry_price:.2f}",
                f"Stop: {a.stop_loss:.2f}",
                f"Target 1: {a.target_1:.2f} | Target 2: {a.target_2:.2f}",
                f"Volume ratio: {a.volume_ratio:.1f}x",
                "",
            ]

            if len("\n".join(current + block)) > 3500 and len(current) > 3:
                chunks.append("\n".join(current))
                current = [
                    f"Stock Breakout Scanner — {run_date.isoformat()}",
                    "",
                ]

            current.extend(block)

        if len(current) > 2:
            chunks.append("\n".join(current))

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        for message in chunks:
            try:
                response = requests.post(
                    url,
                    json={"chat_id": self.chat_id, "text": message},
                    timeout=15,
                )
                response.raise_for_status()
                result = response.json()

                if not result.get("ok"):
                    logger.error("Telegram rejected alert: %s", result)
                else:
                    logger.info("Telegram breakout alert sent")

            except Exception:
                logger.exception("Failed to send Telegram breakout alert")

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
    """Construct enabled alert delivery channels."""

    channels: list[AlertChannel] = []

    if cfg.output.csv:
        channels.append(CSVChannel(cfg.paths.output))

    if cfg.output.telegram:
        if cfg.credentials.has_telegram_breakout_alerts:
            channels.append(
                TelegramChannel(
                    cfg.credentials.telegram_breakout_alerts_bot_token,
                    cfg.credentials.telegram_breakout_alerts_chat_id,
                )
            )
        else:
            logger.error(
                "Telegram is enabled but breakout-alert bot credentials "
                "are missing."
            )

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
