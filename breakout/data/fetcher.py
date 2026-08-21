"""OHLCV data fetcher with pluggable backends.

Two implementations are supported:

    `YFinanceFetcher`    — uses yfinance + NSE `.NS` suffix. No credentials
                           needed. Less reliable for batch use (occasional
                           rate-limiting), but ideal for development.

    `AngelOneFetcher`    — uses Angel One's SmartAPI. Requires API key,
                           Client Code, PIN, and TOTP secret in `.env`.
                           Production-quality but needs an active demat
                           account with API access enabled.

`make_fetcher(cfg)` picks the right backend based on `cfg.data_source`,
falling back to yfinance if Angel One credentials are missing — this lets
the scanner run end-to-end during development before the user has
provisioned SmartAPI access.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from breakout.config import Config


logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Raised when an OHLCV fetch fails after retries."""


# Default polite delay between requests in batch mode. yfinance recommends
# ~0.5s; Angel One has documented rate limits but is more permissive.
_DEFAULT_BATCH_SLEEP_SECONDS = 0.5

_REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

# Retry policy for transient fetch failures (rate limits, flaky responses).
_MAX_FETCH_RETRIES = 3
_RETRY_BASE_DELAY = 1.0     # seconds; exponential backoff 1s, 2s, 4s, ...


class DataFetcher(ABC):
    """Abstract interface for OHLCV fetchers.

    Backends implement `_fetch_once`; the concrete `fetch_history` here wraps it
    with a minimum inter-request throttle (to stay under the backend's rate
    limit) and exponential-backoff retries (to ride out the transient errors
    that show up as fetch failures under load).

    Implementations must return a DataFrame indexed by a `pd.DatetimeIndex` in
    ascending order, with lowercase columns: open, high, low, close, volume.
    Empty or partial responses should raise `FetchError`.
    """

    #: Minimum seconds between requests; 0 disables throttling. Backends set it.
    min_request_interval: float = 0.0

    def __init__(self) -> None:
        self._last_request_ts = 0.0

    @abstractmethod
    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        """Single fetch attempt for `symbol` (no retry/throttle)."""

    def _throttle(self) -> None:
        if self.min_request_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.min_request_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def fetch_history(
        self,
        symbol: str,
        days: int = 300,
        max_retries: int = _MAX_FETCH_RETRIES,
    ) -> pd.DataFrame:
        """Fetch the most recent `days` trading days for `symbol`, with
        throttling and exponential-backoff retries. Raises `FetchError` if all
        attempts fail."""
        last_err: Exception | None = None
        for attempt in range(max_retries):
            self._throttle()
            try:
                return self._fetch_once(symbol, days=days)
            except Exception as e:  # includes FetchError
                last_err = e
                if attempt < max_retries - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.debug(f"retry {symbol} in {delay:.0f}s (attempt {attempt + 1}): {e}")
                    time.sleep(delay)
        raise FetchError(f"{symbol}: failed after {max_retries} attempts: {last_err}") from last_err

    def fetch_history_batch(
        self,
        symbols: list[str],
        days: int = 300,
        sleep_between: float = _DEFAULT_BATCH_SLEEP_SECONDS,
    ) -> dict[str, pd.DataFrame]:
        """Default batch implementation — sequential (throttle handles pacing).

        Subclasses may override for parallel fetches when their backend
        supports it without tripping rate limits.
        """
        results: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                results[sym] = self.fetch_history(sym, days=days)
            except Exception as e:
                logger.warning(f"fetch failed for {sym}: {e}")
        return results


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure lowercase columns + the 5 required fields, sorted ascending."""
    df = df.rename(columns={c: c.lower() for c in df.columns})
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise FetchError(f"missing required columns: {missing}")
    df = df[_REQUIRED_COLS].sort_index()
    df = df.dropna(subset=["close"])
    return df


# ── yfinance backend ─────────────────────────────────────────────────────


class YFinanceFetcher(DataFetcher):
    """Fetches daily OHLCV from yfinance (Yahoo) with NSE `.NS` suffix.

    yfinance accepts a `period` argument like '1y', '2y', '5y'. We choose
    the smallest period that covers the requested number of trading days
    plus a small buffer for holidays.
    """

    min_request_interval = 0.5   # yfinance: ~2 req/s is polite

    def __init__(self, suffix: str = ".NS"):
        super().__init__()
        self.suffix = suffix
        # Lazy import so the package can be loaded without yfinance present.
        import yfinance as yf  # noqa: F401

    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        import yfinance as yf

        ticker = symbol if symbol.endswith(self.suffix) else f"{symbol}{self.suffix}"
        # Choose period: trading days / 252 -> years, rounded up.
        years = max(1, int((days / 252) + 0.99))
        period = f"{years}y"
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval="1d", auto_adjust=False)
        except Exception as e:
            raise FetchError(f"yfinance error for {symbol}: {e}") from e
        if df is None or df.empty:
            raise FetchError(f"yfinance returned empty data for {symbol}")
        return _normalize_dataframe(df)


# ── Angel One backend ────────────────────────────────────────────────────


# Angel One publishes a JSON of all tradable instruments at this URL.
# The format has been stable for years but the URL is worth double-checking
# in their SmartAPI docs if calls start returning empty.
_ANGEL_INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


class AngelOneFetcher(DataFetcher):
    """Fetches daily OHLCV from Angel One's SmartAPI.

    Authenticates once on first request (login + TOTP), then caches the
    SmartConnect handle for the lifetime of the fetcher. The instrument
    master JSON (Symbol -> Token mapping) is also cached on first use.
    """

    # Angel One historical API allows ~3 req/s; keep a safe margin.
    min_request_interval = 0.35

    def __init__(
        self,
        api_key: str,
        client_code: str,
        pin: str,
        totp_secret: str,
    ):
        super().__init__()
        self.api_key = api_key
        self.client_code = client_code
        self.pin = pin
        self.totp_secret = totp_secret
        self._smart: Any | None = None
        self._token_by_symbol: dict[str, str] | None = None

    def _connect(self) -> Any:
        if self._smart is not None:
            return self._smart
        try:
            from SmartApi import SmartConnect
            import pyotp
        except ImportError as e:
            # smartapi-python ships an incomplete requirements list — its
            # source imports `logzero` and `websocket-client` without
            # declaring them as deps. If you see those names in the chain,
            # install them explicitly.
            raise FetchError(
                f"Angel One backend import failed: {e}. "
                "Install: pip install smartapi-python pyotp logzero websocket-client"
            ) from e

        smart = SmartConnect(api_key=self.api_key)
        totp = pyotp.TOTP(self.totp_secret).now()
        resp = smart.generateSession(self.client_code, self.pin, totp)
        if not resp or not resp.get("status"):
            raise FetchError(f"Angel One login failed: {resp}")
        self._smart = smart
        return smart

    def _load_instruments(self) -> dict[str, str]:
        if self._token_by_symbol is not None:
            return self._token_by_symbol
        import requests

        logger.info("fetching Angel One instrument master")
        resp = requests.get(_ANGEL_INSTRUMENT_MASTER_URL, timeout=60)
        resp.raise_for_status()
        instruments = resp.json()
        mapping: dict[str, str] = {}
        for item in instruments:
            if item.get("exch_seg") != "NSE":
                continue
            sym = item.get("symbol", "")
            if sym.endswith("-EQ"):
                # The base symbol (e.g. "RELIANCE" from "RELIANCE-EQ")
                base = sym[:-3]
                mapping[base] = str(item["token"])
        if not mapping:
            raise FetchError("Angel One instrument master returned no NSE equities")
        self._token_by_symbol = mapping
        return mapping

    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        smart = self._connect()
        instruments = self._load_instruments()

        # Symbol normalization — accept "RELIANCE", "RELIANCE-EQ", "RELIANCE.NS"
        base = symbol.replace(".NS", "").replace(".BO", "").upper()
        if base.endswith("-EQ"):
            base = base[:-3]
        token = instruments.get(base)
        if not token:
            raise FetchError(f"no Angel One NSE token for symbol {symbol}")

        # 1.5x calendar-day buffer covers weekends and holidays.
        to_date = datetime.now()
        from_date = to_date - timedelta(days=int(days * 1.5))
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "ONE_DAY",
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            response = smart.getCandleData(params)
        except Exception as e:
            raise FetchError(f"Angel One getCandleData failed for {symbol}: {e}") from e
        if not response or not response.get("data"):
            raise FetchError(f"Angel One returned no candles for {symbol}")

        rows = response["data"]
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        return _normalize_dataframe(df)


# ── Factory ──────────────────────────────────────────────────────────────


def fetch_yf_index(symbol: str, days: int = 90) -> pd.Series | None:
    """Fetch a daily close Series for an index (e.g. '^INDIAVIX') via yfinance.

    Indices aren't tied to the configured stock backend and are reliably on
    yfinance. Returns None on any failure — callers degrade gracefully.
    """
    try:
        import yfinance as yf

        years = max(1, int(days / 252 + 0.99))
        df = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        s = df["Close"].dropna()
        return s if len(s) else None
    except Exception as e:
        logger.warning(f"index fetch failed for {symbol}: {e}")
        return None


def fetch_earnings_dates(symbol: str, suffix: str = ".NS", limit: int = 8) -> list[date]:
    """Best-effort recent + upcoming earnings dates for `symbol` via yfinance.

    Returns [] on any failure or missing data (earnings coverage for Indian
    names is patchy — the blackout filter treats [] as "not in blackout").
    """
    try:
        import yfinance as yf

        ticker = symbol if symbol.endswith(suffix) else f"{symbol}{suffix}"
        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is None or len(df) == 0:
            return []
        out: list[date] = []
        for ts in df.index:
            try:
                out.append(pd.Timestamp(ts).date())
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        logger.debug(f"earnings fetch failed for {symbol}: {e}")
        return []


def make_fetcher(cfg: Config) -> DataFetcher:
    """Return the configured fetcher, with a graceful fallback to yfinance.

    If `cfg.data_source == "angelone"` but the required credentials are not
    set in `.env`, a warning is logged and yfinance is used instead. This
    lets the scanner run end-to-end during development before SmartAPI is
    fully provisioned.
    """
    source = (cfg.data_source or "yfinance").lower()
    if source == "angelone":
        if not cfg.credentials.has_angelone:
            logger.warning(
                "data_source is 'angelone' but ANGELONE_* credentials are "
                "missing from .env — falling back to yfinance for now"
            )
            return YFinanceFetcher()
        return AngelOneFetcher(
            api_key=cfg.credentials.angelone_api_key,
            client_code=cfg.credentials.angelone_client_code,
            pin=cfg.credentials.angelone_pin,
            totp_secret=cfg.credentials.angelone_totp_secret,
        )
    if source == "yfinance":
        return YFinanceFetcher()
    raise ValueError(f"unsupported data_source: {cfg.data_source}")
