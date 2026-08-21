# Build Progress

_Last updated: 2026-07-15_

## Status at a glance

| | Status |
|---|---|
| **Phase 1 (MVP)** | ✅ Complete & verified live (Angel One + Excel storage) |
| **Phase 2 (confirmation layer)** | ✅ Complete (Chunks A–F) |
| **Phase 3 (validation & polish)** | ✅ Core complete (K, I, H, G, J) |
| **Tests** | ✅ 204 passing (`python -m pytest`) |

## What's done

### Phase 1 — complete
- Full pipeline runs end-to-end against **live Angel One** data: `morning_scan` → `preclose_scan` → `eod_settle`. A 500-name scan produced 41 candidates.
- **Storage is a single Excel workbook**: `data_cache/breakout.xlsx`, one sheet per table (config key `paths.workbook`). Migrated from the original SQLite design.
  - ⚠️ **Close `breakout.xlsx` in Excel before running a job**, and let OneDrive finish syncing — an open/locked file makes the job fail with a clear lock error. (Consider excluding `data_cache/` from OneDrive sync.)
- Darvas Box and ascending-triangle detectors were recalibrated so they actually fire on real NSE data (they were matching ~0/500 due to an over-strict "flat" rule).

### Phase 2 — Chunk A complete
- All **9 pattern detectors** are implemented and enabled in `config.yaml`:
  `fifty_two_week_high`, `darvas_box`, `nr7`, `inside_bar`, `bollinger_squeeze`,
  `ascending_triangle`, `vcp`, `cup_and_handle`, `flag`.
- 37 new pattern tests added; all 9 confirmed firing on the cached 500-name universe.

### Phase 2 — Chunk B complete
- `analysis/tightness.py` (ATR-contraction score) and `analysis/rs.py` (relative-strength percentile vs the universe) written with full tests.
- Not yet wired into scoring — that happens in Chunk D.

### Phase 2 — Chunk C complete
- `filters/mood.py` — sector-index trend, India-VIX regime, `market_tradeable` gate, `classify_sector`.
- `filters/earnings.py` — earnings-blackout window (season-aware; post-earnings reaction allowed).
- Not yet wired — Chunk D.

### Phase 2 — Chunk D complete
- `morning_scan.py` now feeds RS (cross-sectional 63-day rank), tightness, a sector-trend proxy, and the earnings gate into the composite score. Verified live (near-breakout leaders now score higher via RS).
- TODO within D's scope, left as hooks: real NIFTY **sector-index** fetch (currently a member-return proxy) and a real **earnings-date** source (`_earnings_dates` returns [] for now).

### Phase 2 — Chunk E complete
- `preclose_scan` now emits `PULLBACK_ENTRY` alerts when a recent breakout retests its level (within 1%) and closes back above on a reversal candle (`_is_pullback_entry`). It already populated the pullback watchlist on confirmations; `morning_scan` prunes stale entries.

### Phase 2 — Chunk F complete
- `output/digest.py` — win rate, avg R, profit factor, avg days held, by-pattern. Run: `python -m breakout.output.digest`.

## Phase 2 + Phase 3 core are complete ✅

Phase 3 delivered: fetch retry/throttle (`fetcher.py`), failed-breakout `EXIT_SIGNAL` (`eod_settle`),
`paper/stats.py`, backtest harness (`breakout/backtest.py`), and the trading-calendar guard
(`trading_calendar.py`) + `scripts/setup_scheduler.ps1`.

### Optional follow-ups left
- ~~Real **NIFTY sector-index** fetch~~ ✅ done (`mood.fetch_sector_trends`; proxy kept as fallback). Verify the yfinance sector symbols pull on a live run.
- ~~Real **earnings-date** source~~ ✅ done (`fetcher.fetch_earnings_dates` via yfinance; best-effort, [] when missing).
- ~~Wire the **India-VIX** market gate into the jobs~~ ✅ done (advisory mood log in morning_scan).
- ~~Top up **NSE holidays**~~ ✅ done — run `.\.venv\Scripts\python.exe scripts\fetch_holidays.py` at each year start (writes `holidays.txt`, auto-merged by `trading_calendar`).
- Register the scheduled tasks: `powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1` (elevated).
- Note: Angel One can **rate-limit** on repeated same-day scans (saw 264/500 fetch fails once) — space runs out.

Telegram/email output stays deferred (CSV-only per current config).

## Handy commands

```powershell
# run tests
.\.venv\Scripts\python.exe -m pytest

# run a scan (reuses cached prices if run again the same day → fast)
.\.venv\Scripts\python.exe -m breakout.jobs.morning_scan
.\.venv\Scripts\python.exe -m breakout.jobs.preclose_scan
```
