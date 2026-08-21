"""Fetcher retry / throttle tests (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from breakout.data import fetcher as fmod
from breakout.data.fetcher import DataFetcher, FetchError


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

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("yf down")

    monkeypatch.setattr(yfinance, "Ticker", _Boom)
    assert fmod.fetch_earnings_dates("RELIANCE") == []
