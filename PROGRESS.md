# Build Progress

_Last updated: 2026-09-12_

> **Open improvement backlog: [IMPROVEMENTS.md](IMPROVEMENTS.md)** — the
> prioritised list from the 2026-09-12 design review. Start there rather than
> here; the top two items (gap fills, transaction costs) are what make the
> paper-trade edge estimate trustworthy enough to act on.

## Status at a glance

| | Status |
|---|---|
| **Phase 1 (MVP)** | ✅ Complete & verified live (Angel One + Excel storage) |
| **Phase 2 (confirmation layer)** | ✅ Complete (Chunks A–F) |
| **Phase 3 (validation & polish)** | ✅ Core complete (K, I, H, G, J) |
| **Data integrity** | ✅ Demerger/split continuity gate + cache re-basing (2026-09-12) |
| **Signal measurement** | ✅ `alert_features` per-alert feature log (2026-09-12) |
| **Tests** | ✅ 263 passing (`python -m pytest`) |

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

---

## Post-build design review — 2026-09-12

A full design review produced a 15-item backlog. **The backlog and its results
live in [`IMPROVEMENTS.md`](IMPROVEMENTS.md)** — read that rather than this
summary, and keep it updated as items land. Status: **12.5 of 15 done**, Tier 1
and Tier 2 complete, 407 tests green.

What changed that affects how you use the system:

- **Expect noticeably fewer alerts.** The volume component used to saturate at
  the confirmation gate (so every alert scored a free 15/15) and extension past
  the pivot was unscored. Both now discriminate, which lowers scores across the
  board. `min_score_to_alert` was deliberately left at 60 so the effect stays
  attributable — re-tune it from `alert_features`, not from a guess.
- **Expect lower R-multiples.** Transaction costs and gap-through-stop fills are
  now modelled. The drop is the correction, not a regression.
- **Position sizes are capped.** Sizing had no capital constraint: the median
  position was 85% of the account and the largest was 342% of it. Now capped at
  `capital ÷ max_concurrent_positions`. **Any paper trade opened before
  2026-09-12 records a position the account could not have held.**
- **Concentration limits are enforced** (8 total, 3 per sector) — previously
  `max_concurrent_positions` was parsed and never read.
- **Two quality gates are inert and say so.** `promoter_pledge` and
  `earnings_blackout` pass every symbol; the morning scan logs a gate audit
  every run. Treat the protection they imply as absent.
- **The backtest prints a health warning.** Its numbers are a regression check,
  not an edge estimate.

Still open: #12 job-failure notification, #13 timezone, #14 storage/OneDrive,
#15 survivorship (document-only), plus two deliberately parked halves —
`atr_pct` scoring (no agreed sign) and promoter pledge (no free source).

## Handy commands

```powershell
# run tests
.\.venv\Scripts\python.exe -m pytest

# run a scan (reuses cached prices if run again the same day → fast)
.\.venv\Scripts\python.exe -m breakout.jobs.morning_scan
.\.venv\Scripts\python.exe -m breakout.jobs.preclose_scan
```
