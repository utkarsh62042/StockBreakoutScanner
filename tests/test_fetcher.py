"""Fetcher retry / throttle tests (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from breakout.data import fetcher as fmod
from breakout.data.fetcher import (
    DataFetcher,
    FetchError,
    RateLimitError,
    YFinanceFetcher,
    _normalize_dataframe,
)


def _df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=3)
    return pd.DataFrame(
        {"open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3, "close": [1.0] * 3, "volume": [1] * 3},
        index=idx,
    )


class _Flaky(DataFetcher):
    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times
        self.calls = 0

    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise FetchError("transient boom")
        return _df()


def test_retry_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    f = _Flaky(fail_times=2)
    out = f.fetch_history("X")
    assert len(out) == 3
    assert f.calls == 3          # failed twice, succeeded on the third


def test_retry_exhausts_and_raises(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    f = _Flaky(fail_times=99)
    with pytest.raises(FetchError):
        f.fetch_history("X", max_retries=3)
    assert f.calls == 3


def test_throttle_waits_between_calls(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(fmod.time, "sleep", lambda s: slept.append(s))
    f = _Flaky(fail_times=0)
    f.min_request_interval = 0.5
    f._last_request_ts = fmod.time.monotonic()   # a request "just happened"
    f.fetch_history("X")
    assert any(s > 0 for s in slept)             # throttle enforced a wait


class _RateLimited(DataFetcher):
    """Always rejects with the real Angel One AB1021 error text."""

    min_request_interval = 1.0

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
        self.calls += 1
        raise FetchError(
            "Angel One getCandleData failed for X: "
            "{'message': 'Too many requests', 'errorcode': 'AB1021'}"
        )


def test_rate_limit_widens_throttle(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    f = _RateLimited()
    with pytest.raises(FetchError):
        f.fetch_history("X", max_retries=3)
    # Three rejections -> multiplier doubled twice (capped by attempts), so the
    # spacing is now well above the 1.0s base.
    assert f.effective_interval > f.min_request_interval


def test_rate_limit_backs_off_longer_than_plain_error(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(fmod.time, "sleep", lambda s: slept.append(s))

    plain = _Flaky(fail_times=99)
    plain.min_request_interval = 0.0
    with pytest.raises(FetchError):
        plain.fetch_history("X", max_retries=2)
    plain_delay = max(slept)

    slept.clear()
    limited = _RateLimited()
    limited.min_request_interval = 0.0
    with pytest.raises(FetchError):
        limited.fetch_history("X", max_retries=2)
    assert max(slept) > plain_delay


def test_circuit_breaker_trips_after_consecutive_rate_limits(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    f = _RateLimited()
    for _ in range(fmod._RATE_LIMIT_CIRCUIT_BREAK - 1):
        with pytest.raises(FetchError) as exc:
            f.fetch_history("X", max_retries=1)
        assert not isinstance(exc.value, RateLimitError)
    with pytest.raises(RateLimitError):
        f.fetch_history("X", max_retries=1)


def test_success_resets_rate_limit_counter(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)

    class _Alternating(_RateLimited):
        def _fetch_once(self, symbol: str, days: int = 300) -> pd.DataFrame:
            self.calls += 1
            if self.calls % 2:
                raise FetchError("Too many requests (AB1021)")
            return _df()

    f = _Alternating()
    # Ten alternating failures never reach the breaker: each success resets it.
    for _ in range(10):
        try:
            f.fetch_history("X", max_retries=1)
        except RateLimitError:
            pytest.fail("circuit breaker tripped on non-consecutive rate limits")
        except FetchError:
            pass


def test_batch_aborts_on_circuit_break(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    f = _RateLimited()
    with pytest.raises(RateLimitError):
        f.fetch_history_batch([f"S{i}" for i in range(50)])
    # Stopped at the breaker rather than grinding through all 50 symbols.
    assert f.calls < 50


# ── batched yfinance download ────────────────────────────────────────────


def _multi_frame(tickers: list[str], periods: int = 3) -> pd.DataFrame:
    """Mimic `yf.download(..., group_by='ticker')`: (ticker, field) columns."""
    idx = pd.date_range("2024-01-01", periods=periods)
    cols = pd.MultiIndex.from_product(
        [tickers, ["Open", "High", "Low", "Close", "Volume"]]
    )
    data = {(t, f): [1.0] * periods for t in tickers for f in
            ["Open", "High", "Low", "Close", "Volume"]}
    return pd.DataFrame(data, index=idx, columns=cols)


def _patch_download(monkeypatch, fn) -> list[list[str]]:
    """Install a fake `yf.download`, returning the list of ticker-groups seen."""
    import yfinance

    seen: list[list[str]] = []

    def _fake(tickers, **kwargs):
        seen.append(list(tickers))
        return fn(list(tickers))

    monkeypatch.setattr(yfinance, "download", _fake)
    return seen


def test_batch_chunks_and_returns_all(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    syms = [f"S{i}" for i in range(120)]
    seen = _patch_download(monkeypatch, lambda tk: _multi_frame(tk))

    f = YFinanceFetcher()
    f.batch_chunk_size = 50
    out = f.fetch_history_batch(syms, days=300)

    assert set(out) == set(syms)
    assert [len(g) for g in seen] == [50, 50, 20]   # chunked, not one-by-one
    assert all(t.endswith(".NS") for g in seen for t in g)


def test_batch_falls_back_for_symbols_yahoo_drops(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)
    # Yahoo silently omits GHOST from the multi-ticker response.
    seen = _patch_download(
        monkeypatch, lambda tk: _multi_frame([t for t in tk if "GHOST" not in t])
    )

    f = YFinanceFetcher()
    singles: list[str] = []

    def _single(symbol, days=300):
        singles.append(symbol)
        return _df()

    monkeypatch.setattr(f, "_fetch_once", _single)
    out = f.fetch_history_batch(["A", "GHOST", "B"], days=300)

    assert set(out) == {"A", "GHOST", "B"}
    assert singles == ["GHOST"]      # only the dropped one was re-fetched
    assert len(seen) == 1


def test_batch_falls_back_when_whole_chunk_fails(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)

    def _boom(tk):
        raise RuntimeError("yahoo 500")

    _patch_download(monkeypatch, _boom)

    f = YFinanceFetcher()
    monkeypatch.setattr(f, "_fetch_once", lambda symbol, days=300: _df())
    out = f.fetch_history_batch(["A", "B"], days=300)
    assert set(out) == {"A", "B"}    # every symbol recovered individually


def test_batch_rate_limit_widens_throttle(monkeypatch) -> None:
    monkeypatch.setattr(fmod.time, "sleep", lambda s: None)

    def _limited(tk):
        raise RuntimeError("YFRateLimitError: Too Many Requests")

    _patch_download(monkeypatch, _limited)

    f = YFinanceFetcher()
    monkeypatch.setattr(f, "_fetch_once", lambda symbol, days=300: _df())
    f.fetch_history_batch(["A", "B"], days=300)
    assert f.effective_interval > f.min_request_interval


def test_normalize_strips_timezone() -> None:
    """`Ticker.history` returns tz-aware bars, `download` tz-naive ones. The
    store can't hold both, so normalization must flatten the zone."""
    idx = pd.date_range("2024-01-01", periods=3, tz="Asia/Kolkata")
    df = pd.DataFrame(
        {"Open": [1.0] * 3, "High": [1.0] * 3, "Low": [1.0] * 3,
         "Close": [1.0] * 3, "Volume": [1] * 3},
        index=idx,
    )
    out = _normalize_dataframe(df)
    assert out.index.tz is None
    # Local wall-clock date is preserved (bars are day-granularity).
    assert str(out.index[0].date()) == "2024-01-01"


def test_fetch_earnings_dates_parses(monkeypatch) -> None:
    import yfinance
    from datetime import date

    idx = pd.to_datetime(["2026-07-20", "2026-04-18"])

    class _FakeTicker:
        def __init__(self, *a, **k):
            pass

        def get_earnings_dates(self, limit=8):
            return pd.DataFrame({"EPS": [1.0, 2.0]}, index=idx)

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    out = fmod.fetch_earnings_dates("RELIANCE")
    assert date(2026, 7, 20) in out and date(2026, 4, 18) in out


def test_fetch_earnings_dates_graceful_on_error(monkeypatch) -> None:
    import yfinance
    from pathlib import Path

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("yf down")

    monkeypatch.setattr(yfinance, "Ticker", _Boom)
    # Mock Path.exists() to return False so CSV fallback also fails
    def mock_exists(self):
        return False
    monkeypatch.setattr(Path, "exists", mock_exists)
    assert fmod.fetch_earnings_dates("RELIANCE") == []
