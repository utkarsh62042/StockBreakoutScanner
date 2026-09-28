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

import dataclasses
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
    # Breakouts cluster by sector, so without this eight positions can be one
    # bet. Defaulted for configs written before the cap existed.
    max_positions_per_sector: int = 3
    # Portfolio circuit breakers: stop opening positions if triggered
    max_portfolio_loss_pct_daily: float = 3.0
    max_portfolio_loss_pct_monthly: float = 10.0
    # Scale down position sizing when market is stressed (VIX up, breadth down)
    scale_positions_in_market_stress: bool = True


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
    # Where the volume score maxes out. Defaulted so a config.yaml written
    # before the scale was widened still loads.
    volume_saturation_ratio: float = 3.0
    # Chase penalty — how far above the breakout level the entry may print
    # before the score starts docking points. See `scoring.extension_penalty`.
    extension_free_pct: float = 2.0
    extension_max_pct: float = 8.0
    extension_max_penalty: float = 10.0


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
    atr_stop_multiplier: float
    target_1_r_multiple: float
    # `alert_ttl_days` was removed 2026-09-12. It governed ALERTED -> CANCELED
    # under the old next-day-open entry model; since trades open as ENTERED at
    # the 3 PM confirmation there is no waiting period for it to expire, and it
    # had no call site. A leftover key in config.yaml is dropped with a warning
    # rather than crashing the load — see `_known_fields`.


@dataclass(frozen=True)
class RegimeConfig:
    """Market-regime gate — see `breakout.filters.regime`.

    The thresholds are priors. `regime` and `breadth_pct` are logged per alert,
    so they can be re-set from realised outcomes once the sample supports it.
    """

    enabled: bool = True
    nifty_symbol: str = "^NSEI"
    nifty_sma: int = 200
    nifty_slope_lookback: int = 20
    breadth_sma: int = 50
    breadth_up_pct: float = 60.0
    breadth_down_pct: float = 40.0
    risk_off_multiplier: float = 0.5
    severe_multiplier: float = 0.25


@dataclass(frozen=True)
class CostsConfig:
    """Round-trip transaction costs — see `breakout.paper.costs`.

    All rates are percentages of turnover for one leg. Defaults match the NSE
    equity-delivery schedule; only `brokerage_*` and `slippage_pct` are
    broker/execution specific.
    """

    enabled: bool = True
    brokerage_pct: float = 0.1
    brokerage_max_inr: float = 20.0
    stt_pct: float = 0.1
    exchange_txn_pct: float = 0.00297
    sebi_turnover_pct: float = 0.0001
    stamp_duty_pct: float = 0.015
    gst_pct: float = 18.0
    slippage_pct: float = 0.05


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

    angelone_api_key: str = ""
    angelone_client_code: str = ""
    angelone_pin: str = ""
    angelone_totp_secret: str = ""
    telegram_bot_token: str = ""  # deprecated: use telegram_breakout_alerts_bot_token and telegram_trades_summary_bot_token
    telegram_chat_id: str = ""  # deprecated: use telegram_breakout_alerts_chat_id and telegram_trades_summary_chat_id
    telegram_breakout_alerts_bot_token: str = ""
    telegram_breakout_alerts_chat_id: str = ""
    telegram_trades_summary_bot_token: str = ""
    telegram_trades_summary_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_to: str = ""

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

    @property
    def has_telegram_breakout_alerts(self) -> bool:
        return bool(
            self.telegram_breakout_alerts_bot_token
            and self.telegram_breakout_alerts_chat_id
        )

    @property
    def has_telegram_trades_summary(self) -> bool:
        return bool(
            self.telegram_trades_summary_bot_token
            and self.telegram_trades_summary_chat_id
        )

    @property
    def has_telegram_notifications(self) -> bool:
        return self.has_telegram_breakout_alerts or self.has_telegram_trades_summary


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
    # Defaulted so a config.yaml written before the `costs:` block existed
    # still loads — with costs on, at the standard NSE rates.
    costs: CostsConfig = field(default_factory=CostsConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    project_root: Path = field(default=PROJECT_ROOT)


def _known_fields(cls, raw: dict | None, section: str) -> dict:
    """Filter `raw` to the fields `cls` actually declares.

    Without this, a key left in `config.yaml` after a setting is retired blows
    up the whole load with an opaque `TypeError: unexpected keyword argument`.
    Unknown keys are dropped with a warning rather than silently — a dropped key
    is usually a retired setting, but it is occasionally a typo, and a typo that
    vanishes without a word is how a threshold quietly stops applying.
    """
    import warnings

    fields = {f.name for f in dataclasses.fields(cls)}
    data = dict(raw or {})
    unknown = sorted(set(data) - fields)
    if unknown:
        warnings.warn(
            f"config.yaml: ignoring unknown key(s) under `{section}`: "
            f"{', '.join(unknown)}. Retired settings can be deleted; anything "
            f"else is probably a typo and is NOT being applied.",
            stacklevel=2,
        )
    return {k: v for k, v in data.items() if k in fields}


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
        telegram_breakout_alerts_bot_token=os.getenv("TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN", ""),
        telegram_breakout_alerts_chat_id=os.getenv("TELEGRAM_BREAKOUT_ALERTS_CHAT_ID", ""),
        telegram_trades_summary_bot_token=os.getenv("TELEGRAM_TRADES_SUMMARY_BOT_TOKEN", ""),
        telegram_trades_summary_chat_id=os.getenv("TELEGRAM_TRADES_SUMMARY_CHAT_ID", ""),
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
        risk=RiskConfig(**_known_fields(RiskConfig, raw["risk"], "risk")),
        scoring_weights=ScoringWeights(
            **_known_fields(ScoringWeights, raw["scoring_weights"], "scoring_weights")
        ),
        thresholds=Thresholds(
            **_known_fields(Thresholds, raw["thresholds"], "thresholds")
        ),
        pivots=PivotConfig(**_known_fields(PivotConfig, raw["pivots"], "pivots")),
        patterns=PatternsConfig(
            **_known_fields(PatternsConfig, raw["patterns"], "patterns")
        ),
        stage_filter=StageFilterConfig(
            **_known_fields(StageFilterConfig, raw["stage_filter"], "stage_filter")
        ),
        output=OutputConfig(**_known_fields(OutputConfig, raw["output"], "output")),
        paper_trading=PaperTradingConfig(
            **_known_fields(PaperTradingConfig, raw["paper_trading"], "paper_trading")
        ),
        paths=paths,
        logging=LoggingConfig(**_known_fields(LoggingConfig, raw["logging"], "logging")),
        credentials=creds,
        costs=CostsConfig(**_known_fields(CostsConfig, raw.get("costs"), "costs")),
        regime=RegimeConfig(**_known_fields(RegimeConfig, raw.get("regime"), "regime")),
    )


def ensure_runtime_dirs(cfg: Config) -> None:
    """Create the cache/logs/output directories if they don't exist yet.

    Idempotent; safe to call at the start of every job.
    """
    for d in (cfg.paths.data_cache, cfg.paths.logs, cfg.paths.output):
        d.mkdir(parents=True, exist_ok=True)
