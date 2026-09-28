"""OHLCV data fetcher with pluggable backends.

Two implementations are supported:

    `YFinanceFetcher`    — uses yfinance + NSE `.NS` suffix. No credentials
                           needed. Fetches up to `batch_chunk_size` tickers per
                           HTTP call, so a 500-name universe costs ~10 requests
                           (~30s) rather than 500. Currently the default.

    `AngelOneFetcher`    — uses Angel One's SmartAPI. Requires API key,
                           Client Code, PIN, and TOTP secret in `.env`. One
                           request per symbol; no batch endpoint exists.

                           CURRENTLY UNUSABLE for full-universe scans: as of
                           2026-09 `getCandleData` returns AB1021 "Too many
                           requests" even at 8s between requests, a known
                           server-side false positive affecting many users
                           (smartapi forum topic 5639) that Angel One has not
                           acknowledged. `config.yaml` is on yfinance because
                           of this. Retry Angel One when they ship a fix.

`make_fetcher(cfg)` picks the right backend based on `cfg.data_source`,
falling back to yfinance if Angel One credentials are missing.

Note that yfinance is an unofficial scrape with no uptime guarantee. The
batch path degrades gracefully — symbols Yahoo omits from a multi-ticker
response are retried individually — but a long-term production setup wants a
paid/official feed.
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


class RateLimitError(FetchError):
    """Raised when the backend keeps rate-limiting us (circuit breaker tripped).

    Distinct from a plain `FetchError` so callers can abort the whole run
    instead of marching through the rest of the universe collecting the same
    error on every symbol.
    """


# Default polite delay between requests in batch mode. yfinance recommends
# ~0.5s; Angel One has documented rate limits but is more permissive.
_DEFAULT_BATCH_SLEEP_SECONDS = 0.5

_REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

# Retry policy for transient fetch failures (rate limits, flaky responses).
_MAX_FETCH_RETRIES = 3
_RETRY_BASE_DELAY = 1.0     # seconds; exponential backoff 1s, 2s, 4s, ...

# Rate-limit handling. A 429/AB1021 is not a normal transient error: retrying
# it on the same cadence just burns the next quota window too. So we back off
# much harder than for other failures, and permanently slow the throttle down
# for the rest of the run (halving back toward the base rate only after a long
# clean streak).
_RATE_LIMIT_BASE_DELAY = 5.0        # seconds; 5s, 10s, 20s, ...
_RATE_LIMIT_MAX_MULTIPLIER = 8.0    # throttle can grow to 8x the base interval
_RATE_LIMIT_DECAY_AFTER = 25        # consecutive OK fetches before easing off
# If this many symbols in a row die to rate limits, the backend has stopped
# serving us — abort rather than spending 20 minutes proving it.
_RATE_LIMIT_CIRCUIT_BREAK = 5

_RATE_LIMIT_MARKERS = ("too many requests", "ab1021", "rate limit", "429", "access denied")


def _is_rate_limit(exc: BaseException) -> bool:
    """True if `exc` looks like a backend rate-limit rejection.

    Both backends surface these as opaque error strings (SmartAPI wraps the
    AB1021 body, yfinance a 429), so matching on the message is the only
    option available to us.
    """
    return any(m in str(exc).lower() for m in _RATE_LIMIT_MARKERS)


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
        # Adaptive throttle: grows on rate limits, decays after a clean streak.
        self._interval_multiplier = 1.0
        self._clean_streak = 0
        self._consecutive_rate_limited_symbols = 0

    @abstractmethod
    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        """Single fetch attempt for `symbol` (no retry/throttle)."""

    @property
    def effective_interval(self) -> float:
        """Current inter-request spacing, including any rate-limit penalty."""
        return self.min_request_interval * self._interval_multiplier

    def _throttle(self) -> None:
        interval = self.effective_interval
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        wait = interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _on_rate_limited(self) -> None:
        """Widen the throttle after a rate-limit rejection."""
        self._clean_streak = 0
        if self._interval_multiplier < _RATE_LIMIT_MAX_MULTIPLIER:
            self._interval_multiplier = min(
                _RATE_LIMIT_MAX_MULTIPLIER, self._interval_multiplier * 2
            )
            logger.warning(
                f"rate limited — slowing to {self.effective_interval:.2f}s between requests"
            )

    def _on_success(self) -> None:
        self._consecutive_rate_limited_symbols = 0
        if self._interval_multiplier <= 1.0:
            return
        self._clean_streak += 1
        if self._clean_streak >= _RATE_LIMIT_DECAY_AFTER:
            self._clean_streak = 0
            self._interval_multiplier = max(1.0, self._interval_multiplier / 2)
            logger.info(
                f"rate limit eased — {self.effective_interval:.2f}s between requests"
            )

    def fetch_history(
        self,
        symbol: str,
        days: int = 300,
        max_retries: int = _MAX_FETCH_RETRIES,
    ) -> pd.DataFrame:
        """Fetch the most recent `days` trading days for `symbol`, with
        throttling and exponential-backoff retries. Raises `FetchError` if all
        attempts fail, or `RateLimitError` if the backend has been rate-limiting
        every symbol for a while (the caller should stop, not keep going)."""
        last_err: Exception | None = None
        rate_limited = False
        for attempt in range(max_retries):
            self._throttle()
            try:
                df = self._fetch_once(symbol, days=days)
            except Exception as e:  # includes FetchError
                last_err = e
                if _is_rate_limit(e):
                    rate_limited = True
                    self._on_rate_limited()
                    delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                else:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                if attempt < max_retries - 1:
                    logger.debug(f"retry {symbol} in {delay:.0f}s (attempt {attempt + 1}): {e}")
                    time.sleep(delay)
            else:
                self._on_success()
                return df

        if rate_limited:
            self._consecutive_rate_limited_symbols += 1
            if self._consecutive_rate_limited_symbols >= _RATE_LIMIT_CIRCUIT_BREAK:
                raise RateLimitError(
                    f"{self.__class__.__name__}: rate limited on "
                    f"{self._consecutive_rate_limited_symbols} symbols in a row "
                    f"(last: {symbol}) — giving up on this run. Wait for the "
                    f"quota window to reset and re-run."
                ) from last_err
        else:
            self._consecutive_rate_limited_symbols = 0
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
            except RateLimitError:
                raise      # circuit breaker — stop the batch, don't churn
            except Exception as e:
                logger.warning(f"fetch failed for {sym}: {e}")
        return results


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure lowercase columns + the 5 required fields, sorted ascending.

    The index is forced tz-naive: yfinance's `Ticker.history` returns bars
    stamped Asia/Kolkata while its multi-ticker `download` returns tz-naive
    ones, and a store holding both can't be compared or joined. These are daily
    bars, so dropping the zone loses nothing.
    """
    df = df.rename(columns={c: c.lower() for c in df.columns})
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise FetchError(f"missing required columns: {missing}")
    df = df[_REQUIRED_COLS].sort_index()
    df = df.dropna(subset=["close"])
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ── yfinance backend ─────────────────────────────────────────────────────


class YFinanceFetcher(DataFetcher):
    """Fetches daily OHLCV from yfinance (Yahoo) with NSE `.NS` suffix.

    yfinance accepts a `period` argument like '1y', '2y', '5y'. We choose
    the smallest period that covers the requested number of trading days
    plus a small buffer for holidays.

    Bars are fully adjusted (`auto_adjust=True`), which matters less than it
    looks: yfinance back-adjusts **splits and bonuses regardless of the flag**
    (verified — HEG's 5:1 split of 2024-10-18 shows no cliff with
    `auto_adjust=False`). The flag only adds **dividend** back-adjustment, worth
    ~1-2% on older bars. We take it because it puts the whole series on one
    basis, which is what the 63-day relative-strength return and the long
    52-week / 30-week windows are implicitly assuming.

    What neither setting fixes is a **demerger or capital reduction** — Yahoo
    does not model these as splits, so the price drop stays in the series as if
    it were real trading. On NSE that is the common case, not an edge case
    (VEDL, ABFRL, TMPV, TRENT all demerged within the cached window), and a
    -65% phantom bar wrecks the 52-week high, every base detector, ATR and so
    the stop, the SMA slope and so the stage. There is no feed-level remedy;
    `data.validate` is the defence, and it is load-bearing rather than a
    backstop.
    """

    min_request_interval = 0.5   # yfinance: ~2 req/s is polite

    #: Tickers per `yf.download` call. Yahoo accepts a few hundred, but large
    #: chunks make one flaky ticker retry the whole group, and the URL grows
    #: unwieldy. 50 keeps a 500-name universe to ~10 requests.
    batch_chunk_size = 50

    def __init__(self, suffix: str = ".NS"):
        super().__init__()
        self.suffix = suffix
        # Lazy import so the package can be loaded without yfinance present.
        import yfinance as yf  # noqa: F401

    def _ticker(self, symbol: str) -> str:
        return symbol if symbol.endswith(self.suffix) else f"{symbol}{self.suffix}"

    @staticmethod
    def _period_for(days: int) -> str:
        """Smallest yfinance period covering `days` trading days."""
        return f"{max(1, int((days / 252) + 0.99))}y"

    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        import yfinance as yf

        ticker = self._ticker(symbol)
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=self._period_for(days), interval="1d", auto_adjust=True)
        except Exception as e:
            raise FetchError(f"yfinance error for {symbol}: {e}") from e
        if df is None or df.empty:
            raise FetchError(f"yfinance returned empty data for {symbol}")
        return _normalize_dataframe(df)

    def _download_chunk(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        """One multi-ticker `yf.download` call. Returns only the symbols that
        came back with usable bars — callers retry the rest individually."""
        import yfinance as yf

        tickers = [self._ticker(s) for s in symbols]
        raw = yf.download(
            tickers,
            period=self._period_for(days),
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
            actions=False,
        )
        if raw is None or raw.empty:
            raise FetchError(f"yfinance returned nothing for {len(symbols)} tickers")

        out: dict[str, pd.DataFrame] = {}
        for sym, tk in zip(symbols, tickers):
            try:
                # A multi-ticker download yields (ticker, field) columns; a
                # single-ticker one collapses to plain field columns.
                sub = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue     # Yahoo dropped this ticker entirely
            if sub is None or sub.empty:
                continue
            try:
                df = _normalize_dataframe(sub.dropna(how="all"))
            except FetchError:
                continue
            if not df.empty:
                out[sym] = df
        return out

    def fetch_history_batch(
        self,
        symbols: list[str],
        days: int = 300,
        sleep_between: float = _DEFAULT_BATCH_SLEEP_SECONDS,
    ) -> dict[str, pd.DataFrame]:
        """Fetch many symbols using Yahoo's multi-ticker endpoint.

        Roughly an order of magnitude faster than one request per symbol: a
        500-name universe becomes ~10 HTTP calls. Symbols missing from a chunk's
        response (Yahoo silently drops some) fall back to an individual fetch,
        so the result is as complete as the sequential path.
        """
        results: dict[str, pd.DataFrame] = {}
        missing: list[str] = []

        chunks = [
            symbols[i:i + self.batch_chunk_size]
            for i in range(0, len(symbols), self.batch_chunk_size)
        ]
        for n, chunk in enumerate(chunks, 1):
            self._throttle()
            try:
                got = self._download_chunk(chunk, days=days)
            except Exception as e:
                if _is_rate_limit(e):
                    self._on_rate_limited()
                logger.warning(f"batch {n}/{len(chunks)} failed ({e}) — falling back per symbol")
                missing.extend(chunk)
                continue
            results.update(got)
            absent = [s for s in chunk if s not in got]
            missing.extend(absent)
            logger.debug(f"batch {n}/{len(chunks)}: {len(got)}/{len(chunk)} symbols")

        if missing:
            logger.info(f"retrying {len(missing)} symbols individually")
            for sym in missing:
                try:
                    results[sym] = self.fetch_history(sym, days=days)
                except RateLimitError:
                    raise    # circuit breaker — stop, don't churn
                except Exception as e:
                    logger.debug(f"fetch failed for {sym}: {e}")
        return results


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

    CAUTION: `getCandleData` returns **unadjusted** candles — Angel One does
    not back-adjust for splits or bonuses. Unlike the yfinance backend (see
    `YFinanceFetcher`), re-enabling this one reintroduces split cliffs into the
    history. `Store.upsert_prices` re-bases cached bars when a feed's
    adjustment changes, but it cannot invent an adjustment the feed never
    applied; `data.validate.find_price_discontinuity` is the backstop that
    keeps a corrupted series from producing signals.
    """

    # Angel One's published limit for getCandleData is 3 req/s, but in practice
    # a burst anywhere near that trips AB1021 ("Too many requests") a few dozen
    # symbols into a full-universe scan — the per-minute quota is the binding
    # one. 1 req/s walks a 500-name universe in ~8 min without complaints; the
    # adaptive throttle widens this further if we still get rejected.
    min_request_interval = 1.0

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
    """Fetch recent + upcoming earnings dates for `symbol`.

    Tries multiple sources in order:
    1. yfinance (limited NSE coverage, but worth a try)
    2. Local CSV fallback at `earnings_calendar.csv` if it exists
    3. Returns [] if none available (blackout filter treats as "not in blackout")

    ⚠️  WARNING: yfinance has ~no earnings coverage for NSE stocks (verified:
    RELIANCE, TCS, INFY all return []). For production use, integrate with:
    - NSE announcements API (https://archives.nseindia.com/)
    - Screener.in API (free tier available)
    - Manual CSV: earnings_calendar.csv in project root

    Returns [] on any failure or missing data.
    """
    out: list[date] = []

    # Try 1: yfinance (will likely fail for NSE stocks)
    try:
        import yfinance as yf

        ticker = symbol if symbol.endswith(suffix) else f"{symbol}{suffix}"
        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is not None and len(df) > 0:
            for ts in df.index:
                try:
                    out.append(pd.Timestamp(ts).date())
                except (TypeError, ValueError):
                    continue
            if out:
                logger.debug(f"{symbol}: {len(out)} earnings dates from yfinance")
                return out
    except Exception as e:
        logger.debug(f"yfinance earnings fetch failed for {symbol}: {e}")

    # Try 2: Local CSV fallback
    # Create earnings_calendar.csv with columns: symbol, date (YYYY-MM-DD)
    try:
        from pathlib import Path
        csv_path = Path(__file__).resolve().parent.parent.parent / "earnings_calendar.csv"
        if csv_path.exists():
            import csv
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("symbol", "").strip().upper() == symbol.upper():
                        try:
                            ed = pd.to_datetime(row.get("date")).date()
                            if ed >= date.today() - timedelta(days=60):  # Last 60 days
                                out.append(ed)
                        except (ValueError, TypeError):
                            continue
            if out:
                logger.debug(
                    f"{symbol}: {len(out)} earnings dates from CSV fallback"
                )
                return sorted(out)
    except Exception as e:
        logger.debug(f"CSV fallback earnings fetch failed: {e}")

    # No data found
    logger.debug(
        f"{symbol}: no earnings dates found. "
        f"For production, add earnings_calendar.csv or integrate NSE API."
    )
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
