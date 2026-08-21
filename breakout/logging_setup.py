"""Logging configuration.

Centralised so every job uses the same handlers and format. Logs go to
both `logs/YYYY-MM-DD.log` (DEBUG and above) and the console (INFO and
above by default; can be lifted to DEBUG via config).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(logs_dir: Path, level: str = "INFO", console: bool = True) -> None:
    """Configure root logger. Idempotent (replaces any existing handlers)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{date.today().isoformat()}.log"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)

    if console:
        rich_handler = RichHandler(
            level=getattr(logging, level.upper(), logging.INFO),
            show_time=True,
            show_path=False,
            markup=False,
        )
        root.addHandler(rich_handler)
