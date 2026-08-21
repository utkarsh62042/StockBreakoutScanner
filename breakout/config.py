"""Configuration loader.

Reads `config.yaml` at the project root and merges credentials from `.env`.
Returns a frozen, typed `Config` object so the rest of the codebase can
access settings via attribute access (e.g. `cfg.risk.capital`) instead of
fragile dict lookups.

Path resolution is anchored at the project root (the parent of this file's
package), so jobs work the same whether invoked from the project directory
or another working directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RiskConfig:
    capital: float
    risk_per_trade_pct: float
    max_concurrent_positions: int


@dataclass(frozen=True)
class ScoringWeights:
    pattern_quality: float
    stage: float
    relative_strength: float
    volume: float
    tightness: float
    sector: float


@dataclass(frozen=True)
class Thresholds:
    min_score_to_alert: float
    min_volume_ratio: float
    min_market_cap_cr: float
    min_adv_cr: float
    min_listing_days: int
    near_breakout_pct: float
    pullback_window_days: int
    pullback_retest_band_pct: float


@dataclass(frozen=True)
class PivotConfig:
    swing_n: int


@dataclass(frozen=True)
class PatternsConfig:
    enabled: list[str]


@dataclass(frozen=True)
class StageFilterConfig:
    sma_weeks: int
    slope_lookback_weeks: int
    high_lookback_weeks: int


@dataclass(frozen=True)
class OutputConfig:
    csv: bool
    telegram: bool
    email: bool


@dataclass(frozen=True)
class PaperTradingConfig:
    enabled: bool
    hold_max_days: int
    alert_ttl_days: int
    atr_stop_multiplier: float
    target_1_r_multiple: float


@dataclass(frozen=True)
class PathsConfig:
    data_cache: Path
    logs: Path
    output: Path
    workbook: Path


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    console: bool


@dataclass(frozen=True)
class Credentials:
    """Sensitive values pulled from .env. Empty strings if not set."""

    angelone_api_key: str
    angelone_client_code: str
    angelone_pin: str
    angelone_totp_secret: str
    telegram_bot_token: str
    telegram_chat_id: str
    smtp_host: str
    smtp_port: str
    smtp_user: str
    smtp_password: str
    smtp_to: str

    @property
    def has_angelone(self) -> bool:
        return all(
            [
                self.angelone_api_key,
                self.angelone_client_code,
                self.angelone_pin,
                self.angelone_totp_secret,
            ]
        )

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@dataclass(frozen=True)
class Config:
    universe: str
    data_source: str
    risk: RiskConfig
    scoring_weights: ScoringWeights
    thresholds: Thresholds
    pivots: PivotConfig
    patterns: PatternsConfig
    stage_filter: StageFilterConfig
    output: OutputConfig
    paper_trading: PaperTradingConfig
    paths: PathsConfig
    logging: LoggingConfig
    credentials: Credentials
    project_root: Path = field(default=PROJECT_ROOT)


def _resolve_path(p: str, root: Path) -> Path:
    """Resolve a path string from config — absolute paths pass through,
    relative paths are anchored at the project root."""
    path = Path(p)
    return path if path.is_absolute() else (root / path)


def load_config(config_path: Path | str | None = None) -> Config:
    """Load and validate config.yaml + .env.

    Args:
        config_path: Optional override. Defaults to PROJECT_ROOT/config.yaml.

    Returns:
        A frozen Config with all nested settings resolved.

    Raises:
        FileNotFoundError: if config.yaml is missing.
        ValueError: if a required field is missing or has the wrong type.
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            f"Copy config.example.yaml to config.yaml and edit it."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # Load .env from project root (if present); never error if missing.
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    creds = Credentials(
        angelone_api_key=os.getenv("ANGELONE_API_KEY", ""),
        angelone_client_code=os.getenv("ANGELONE_CLIENT_CODE", ""),
        angelone_pin=os.getenv("ANGELONE_PIN", ""),
        angelone_totp_secret=os.getenv("ANGELONE_TOTP_SECRET", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=os.getenv("SMTP_PORT", ""),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_to=os.getenv("SMTP_TO", ""),
    )

    paths_raw = raw["paths"]
    paths = PathsConfig(
        data_cache=_resolve_path(paths_raw["data_cache"], PROJECT_ROOT),
        logs=_resolve_path(paths_raw["logs"], PROJECT_ROOT),
        output=_resolve_path(paths_raw["output"], PROJECT_ROOT),
        workbook=_resolve_path(paths_raw["workbook"], PROJECT_ROOT),
    )

    return Config(
        universe=raw["universe"],
        data_source=raw["data_source"],
        risk=RiskConfig(**raw["risk"]),
        scoring_weights=ScoringWeights(**raw["scoring_weights"]),
        thresholds=Thresholds(**raw["thresholds"]),
        pivots=PivotConfig(**raw["pivots"]),
        patterns=PatternsConfig(**raw["patterns"]),
        stage_filter=StageFilterConfig(**raw["stage_filter"]),
        output=OutputConfig(**raw["output"]),
        paper_trading=PaperTradingConfig(**raw["paper_trading"]),
        paths=paths,
        logging=LoggingConfig(**raw["logging"]),
        credentials=creds,
    )


def ensure_runtime_dirs(cfg: Config) -> None:
    """Create the cache/logs/output directories if they don't exist yet.

    Idempotent; safe to call at the start of every job.
    """
    for d in (cfg.paths.data_cache, cfg.paths.logs, cfg.paths.output):
        d.mkdir(parents=True, exist_ok=True)
