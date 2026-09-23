"""Logging configuration.

Centralised so every job uses the same handlers and format. Logs go to
both `logs/YYYY-MM-DD.log` (DEBUG and above) and the console (INFO and
above by default; can be lifted to DEBUG via config).
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

from breakout.analysis.session import today_ist


def setup_logging(logs_dir: Path, level: str = "INFO", console: bool = True) -> None:
    """Configure root logger. Idempotent (replaces any existing handlers)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{today_ist().isoformat()}.log"

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

    # smartapi-python logs every HTTP failure itself (via logzero) with the full
    # request headers and body. We already log fetch failures with the context
    # that matters, and its version drowns out our own retry/backoff lines.
    # logzero attaches its own handler with propagate=False, so silencing these
    # loggers drops those lines entirely — that's intended.
    logging.getLogger("logzero_default").setLevel(logging.CRITICAL)
    logging.getLogger("smartConnect").setLevel(logging.CRITICAL)

    if console:
        # yfinance logs its own ERROR for every delisted/404 ticker, so one bad
        # symbol paints six red lines on a scan that is otherwise fine. We log
        # the failures ourselves with the context that matters, so keep Yahoo's
        # version in the file (for debugging) but drop it from the console.
        noisy = ("yfinance", "peewee", "urllib3")
        rich_handler = RichHandler(
            level=getattr(logging, level.upper(), logging.INFO),
            show_time=True,
            show_path=False,
            markup=False,
        )
        rich_handler.addFilter(
            lambda record: not record.name.split(".")[0] in noisy
        )
        root.addHandler(rich_handler)
