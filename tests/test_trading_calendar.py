"""Trading-calendar tests."""

from __future__ import annotations

from datetime import date

from breakout.trading_calendar import (
    active_holidays,
    is_trading_day,
    load_holidays,
    parse_nse_holidays,
    require_trading_day,
)


def test_weekday_is_trading_day() -> None:
    assert is_trading_day(date(2026, 8, 3)) is True    # Monday


def test_weekend_is_not_trading_day() -> None:
    assert is_trading_day(date(2026, 8, 8)) is False    # Saturday
    assert is_trading_day(date(2026, 8, 9)) is False    # Sunday


def test_holiday_is_not_trading_day() -> None:
    assert is_trading_day(date(2026, 1, 26)) is False    # Republic Day


def test_custom_holiday_set() -> None:
    d = date(2026, 8, 3)
    assert is_trading_day(d, holidays={d}) is False


def test_require_trading_day_returns_bool() -> None:
    assert require_trading_day(date(2026, 8, 3)) is True
    assert require_trading_day(date(2026, 1, 26)) is False


def test_load_holidays(tmp_path) -> None:
    p = tmp_path / "hol.txt"
    p.write_text("# NSE 2026\n2026-03-19\n\n2026-11-09\n", encoding="utf-8")
    hols = load_holidays(p)
    assert hols == {date(2026, 3, 19), date(2026, 11, 9)}


def test_load_holidays_missing_file(tmp_path) -> None:
    assert load_holidays(tmp_path / "nope.txt") == set()


def test_parse_nse_holidays() -> None:
    payload = {
        "CM": [
            {"tradingDate": "26-Jan-2026", "description": "Republic Day"},
            {"tradingDate": "19-Mar-2026", "description": "Holi"},
            {"tradingDate": "bad-date", "description": "junk"},
        ],
        "FO": [{"tradingDate": "01-Jan-2027"}],
    }
    out = parse_nse_holidays(payload)
    assert out == [date(2026, 1, 26), date(2026, 3, 19)]   # CM only, bad row skipped, sorted


def test_parse_nse_holidays_fallback_and_empty() -> None:
    assert parse_nse_holidays({"XX": [{"tradingDate": "19-Mar-2026"}]}) == [date(2026, 3, 19)]
    assert parse_nse_holidays({}) == []


def test_active_holidays_merges_file(tmp_path) -> None:
    p = tmp_path / "holidays.txt"
    p.write_text("2026-03-19\n", encoding="utf-8")
    hols = active_holidays(p)
    assert date(2026, 3, 19) in hols          # from file
    assert date(2026, 1, 26) in hols          # built-in still present

