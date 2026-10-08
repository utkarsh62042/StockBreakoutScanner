# Breakout Scanner — Complete Guide

A read-once, refer-back document covering every part of the system: what it does, why it's built the way it is, what each file is for, how to run it, and what the output means.

> If you're trying to *use* the scanner, jump to [§ 15 Step-by-step](#15-step-by-step-running-the-system). If you're trying to *understand* it, read top to bottom.

---

## Table of Contents

1. [What this system does](#1-what-this-system-does)
2. [The trading strategy in plain English](#2-the-trading-strategy-in-plain-english)
3. [Why these technology choices](#3-why-these-technology-choices)
4. [Architecture: the five layers](#4-architecture-the-five-layers)
5. [File-by-file map](#5-file-by-file-map)
6. [Indicators: what they measure and why](#6-indicators-what-they-measure-and-why)
7. [Pivots: the foundation of everything](#7-pivots-the-foundation-of-everything)
8. [Pattern detectors](#8-pattern-detectors)
9. [Stage classification (Stan Weinstein)](#9-stage-classification-stan-weinstein)
10. [Gates and filters](#10-gates-and-filters)
11. [Composite scoring](#11-composite-scoring)
12. [Paper trading](#12-paper-trading)
13. [The three daily jobs](#13-the-three-daily-jobs)
14. [Database schema](#14-database-schema)
15. [Step-by-step: running the system](#15-step-by-step-running-the-system)
16. [Expected outputs](#16-expected-outputs)
17. [Phase status (what's done, what's coming)](#17-phase-status)
18. [Configuration reference](#18-configuration-reference)
19. [Troubleshooting](#19-troubleshooting)
20. [Glossary](#20-glossary)

---

## 1. What this system does

### The problem it solves

Picking stocks to trade in the NSE NIFTY 500 universe is a needle-in-haystack problem. Even if you know how to read a chart, manually scanning 500 stocks every day for high-probability breakout setups is a multi-hour job. Most discretionary traders end up watching the same 30–50 names they're familiar with, missing setups elsewhere.

This system automates the scan. It looks at all 500 stocks twice a day, applies a deterministic rule-set, and tells you which ones are setting up to break out and which are confirming breakouts in real time. You retain final discretion — the system produces a ranked list, you decide what to act on.

### What you put in

- A laptop running Windows / macOS / Linux with Python 3.10+ installed.
- An Angel One demat account with SmartAPI access (free with the account). yfinance works as a credential-free fallback during development.
- A `config.yaml` with your capital and risk preferences (already set up: ₹2,00,000 capital, 2.5% risk/trade).
- Roughly 10 minutes of attention per trading day to read the alerts and decide what to act on.

### What you get out

Daily during trading hours:

- **A setup watchlist** — stocks that are *near* breaking out (within 2% of their breakout level) and have passed all quality and stage filters.
- **Alerts (CSV file)** — issued in the last 25 minutes of the trading session for stocks that *actually confirmed* a breakout that day (closed above level with above-average volume). Each alert includes precise entry, stop-loss, two targets, and position size sized to your risk-per-trade setting.
- **A paper trade audit log** — every alert is automatically logged as a virtual position and tracked through the same state machine a real trade would follow. After 4–6 weeks you have data on win rate, average R-multiple, and which patterns are working in current market conditions.

### What it is NOT

- **Not a robot trader.** It produces alerts. Execution is manual via your broker app.
- **Not a price predictor or ML model.** Every rule is hand-coded and inspectable. Nothing is learned from data; you can read every threshold in the config or source.
- **Not real-time.** It runs as scheduled batch jobs (currently triggered manually; Windows Task Scheduler entries can be added later). Decisions are made on daily-close bars, not tick data.
- **Not SEBI-registered investment advice.** It's a personal tool. Use the paper trade log to build confidence before risking real capital; even then, all trading involves risk of loss.

---

## 2. The trading strategy in plain English

The system codifies a five-layer decision process. A stock must pass *every* layer in order to be alerted.

### Layer 1 — Quality floor

Reject anything that's manipulated, illiquid, or just-listed. Specifically:
- Market cap ≥ ₹500 cr (rules out penny stocks)
- Average daily turnover ≥ ₹5 cr over the last 20 days (you can actually buy and sell without moving the price)
- Listed at least 365 days (lets the chart history form properly)
- Promoter pledge < 30% (Phase 2 will wire this up; currently default-pass)

### Layer 2 — Stage filter (Stan Weinstein)

Only trade stocks in *Stage 2* — confirmed uptrends. Specifically: price above a rising 30-week (150-day) simple moving average, with recent 10-week highs exceeding prior 10-week highs.

This single filter historically eliminates ~70% of false breakouts. The intuition is brutal but consistent: a breakout in a downtrend is overwhelmingly likely to fail. The market makes most of its gains in Stage 2 advances; the other three stages are at best sideways, at worst destructive.

### Layer 3 — Pattern detection

For surviving stocks, look for one of nine chart patterns indicating a tradable consolidation:

| # | Pattern | What it looks for | Phase |
|---|---|---|---|
| 1 | 52-week high breakout | Close within 2% of the 52w high after a flat-resistance period | 1 ✓ |
| 2 | Darvas Box | 20+ day consolidation between a flat ceiling (±3%) and flat floor (±5%) with 3+ ceiling touches | 1 ✓ |
| 3 | NR7 | Today's true range is the smallest of the last 7 days (energy compression) | 1 ✓ |
| 4 | Inside bar breakout | Today's bar entirely contained within yesterday's | 2 |
| 5 | Bollinger Band squeeze | BB width at 6-month minimum with price in upper half | 2 |
| 6 | Ascending triangle | Horizontal resistance with rising support trendline | 2 |
| 7 | VCP (Volatility Contraction) | Sequence of 3+ pullbacks each shallower than the last | 2 |
| 8 | Cup & Handle | U-shape base followed by shallow handle pullback | 2 |
| 9 | Flag | Sharp directional move followed by tight counter-trend channel | 2 |

Each pattern returns a 0–100 confidence score for *how cleanly* the structure fits, plus the *breakout level* (the price the stock needs to close above).

### Layer 4 — Confirmation signals (scored)

A clean pattern by itself is necessary but not sufficient. Multiple confirming signals raise the score:

- **Volume on the breakout candle** — at least 1.5× the 20-day average. Institutional commitment shows up in volume.
- **Tightness** — pre-breakout volatility contraction (ATR(10) decreasing relative to ATR(50)). Tighter base = sharper move when the breakout happens.
- **Relative strength** — the stock's 63-day return vs the NIFTY 500. Top 25% adds points; below median adds nothing.
- **Sector trend** — the sector index (NIFTY Bank, IT, Auto, etc.) trending up is a tailwind.

### Layer 5 — Composite score

All of the above roll into a single 0–100 score:

| Component | Max points |
|---|---|
| Pattern quality | 35 |
| Stage (Stage 2 = 20, else 0) | 20 |
| Volume ratio | 15 |
| Relative strength | 15 |
| Tightness | 10 |
| Sector trend | 5 |
| **Total** | **100** |

Stocks scoring ≥ 50 go on the watchlist. Stocks scoring ≥ 60 *and* confirming the breakout pre-close get an alert.

### Why this strategy works (the thesis)

1. **Breakouts from clean bases historically outperform random entries.** Studies by William O'Neil (CAN SLIM), Mark Minervini (VCP), Stan Weinstein, and others all show this. The compounding of multiple independent filters (stage + pattern + volume) is more reliable than any single filter alone.
2. **Closing breakouts outperform intraday breakouts.** Institutional commitment shows in the final 25 minutes of the session. Stocks that break out at 11 AM and sell off by 2 PM are noise; stocks that close at highs on volume are real.
3. **Paper trading the same logic for 4–6 weeks gives you a real edge estimate** before risking capital. Backtest results are not predictive (regimes change), but a few weeks of forward paper trading in the *current* regime is.

### When this strategy fails

- **Choppy / sideways markets** — patterns form and break repeatedly. Win rate drops; expect fewer alerts and lower R-multiples.
- **Sharp directional regimes** (sustained bull or bear) — bull markets reward this strategy; bear markets starve it of Stage 2 candidates.
- **Earnings season** — patterns get disrupted by news. The earnings blackout filter (Phase 2) reduces this.
- **Liquidity events** (election, RBI policy, FII flows) — pattern reliability drops temporarily. No filter for this; treat outsized days with skepticism.

---

## 3. Why these technology choices

| Choice | Why |
|---|---|
| **Python 3.10+** | pandas + numpy + scipy ecosystem is unmatched for tabular financial data. 3.10+ for modern type hints and pattern matching. |
| **`uv` (or pip) + venv** | Reproducible environment. The venv keeps dependencies isolated from your system Python. |
| **pandas + numpy** | OHLCV data is naturally tabular. pandas' rolling-window operations are exactly what indicators need. |
| **scipy** | Used in Phase 2 for linear regression on swing points (ascending triangle, VCP). Already imported now to avoid a future install. |
| **Excel workbook (openpyxl)** | Single `.xlsx` file, one sheet per table. No server, no setup, and the user can open it directly in Excel to inspect or hand-edit state. Loaded fully into memory on open and flushed on close (fast enough for our scale, ~500 symbols × 250 days = 125k rows). A SQL backend may return later; the `Store` API is storage-agnostic. |
| **yfinance** | Free, no account needed, good enough for development and as a fallback when Angel One is unreachable. Occasional rate-limiting and occasional empty responses (saw HDFCBANK / MARUTI fail in early testing). |
| **Angel One SmartAPI** | Production data source. Fast, reliable, free with a demat account. The `smartapi-python` package handles the HTTP plumbing; we layer on auth + symbol token lookup. |
| **`pyotp`** | Generates the TOTP code Angel One requires on every login. |
| **`python-dotenv`** | Loads `.env` into environment variables. Keeps credentials out of `config.yaml` (which can be safely committed) and out of source. |
| **`rich`** | Pretty console tables and colored log output. Optional but the CSV alone is less scannable than a `rich.Table`. |
| **`pytest`** | Standard. 78 tests across pivots / indicators / patterns / stage / end-to-end. |
| **`requests`** | For the NSE CSV download and email dispatch. |
| **`pyyaml`** | Config file format. Human-readable + structured. |

### Why NOT these technologies

- **No deep learning frameworks (tensorflow, torch).** This is a rule-based system. ML adds opacity ("why did the model alert?") and overfitting risk on a small dataset. Every rule here is inspectable and tunable.
- **No web framework (Flask, FastAPI).** No server, no API. Two batch jobs a day. A dashboard would be more friction than the CSV.
- **No real-time data (websockets, streaming).** Decisions are made on closed daily bars. Real-time data isn't needed and complicates the architecture.
- **No multi-user infra.** Single-user tool. Auth, sessions, user models — all unnecessary.
- **No cloud hosting.** Runs on your laptop. Faster, free, private.

---

## 4. Architecture: the five layers

```
┌─────────────────────────────────────────────────────────────┐
│ DATA LAYER                                                  │
│ Universe (NIFTY 500) → Fetcher (yfinance/Angel One)         │
│ → Excel workbook cache (~125k rows of OHLCV)                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ ANALYSIS LAYER (per symbol, pure functions)                 │
│ Pivots → Indicators (RSI, ATR, BB, SMA, ...)                │
│ Stage classifier (1/2/3/4/UNKNOWN)                          │
│ Pattern detectors (3 in Phase 1; 9 in Phase 2)              │
│ Tightness / RS / Sector  ← Phase 2                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ FILTER LAYER (hard pass/fail)                               │
│ Quality (mcap, ADV, listing age, pledge)                    │
│ Earnings blackout  ← Phase 2                                │
│ Market mood (VIX, sector index)  ← Phase 2                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ SCORING ENGINE                                              │
│ Pattern (35) + Stage (20) + RS (15) + Volume (15)           │
│ + Tightness (10) + Sector (5) = Composite (0–100)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ OUTPUT LAYER                                                │
│ Watchlist (workbook) → Alerts (CSV)                         │
│ Paper trades (workbook, lifecycle: ALERTED→ENTERED→...)     │
└─────────────────────────────────────────────────────────────┘
```

Each layer is independent — you can audit indicators without running patterns, run patterns without scoring, score without dispatching. The clean separation is intentional: it lets you debug *where* a setup got filtered out (`skip summary` in morning_scan logs shows exactly this).

---

## 5. File-by-file map

```
BreakoutStockAnalyser/
│
├── README.md                  Project overview (user-facing)
├── BUILD_PROMPT.md            The spec used to build the system
├── GUIDE.md                   This file
├── pyproject.toml             Python package definition + dependencies
├── config.yaml                Your settings (capital, risk, thresholds)
├── config.example.yaml        Template if you need to recreate config.yaml
├── .env                       Sensitive credentials (gitignored)
├── .env.example               Template showing which env vars are needed
├── .gitignore                 Files git should never track
├── test_smartapi.py           One-off Angel One verifier (you ran this)
├── abc.py                     Your scratch RELIANCE pivot test
│
├── breakout/                  THE PACKAGE
│   ├── __init__.py            Just defines __version__
│   ├── config.py              Loads config.yaml + .env into a Config dataclass
│   ├── logging_setup.py       Configures the rich-console + file logger
│   ├── scoring.py             Composite score (0-100) from features
│   │
│   ├── data/
│   │   ├── universe.py        Downloads NIFTY 500 list from NSE archives, caches 7 days
│   │   ├── fetcher.py         Abstract DataFetcher + YFinanceFetcher + AngelOneFetcher
│   │   └── store.py           Excel-workbook wrapper: prices, universe, watchlists, paper trades
│   │
│   ├── analysis/
│   │   ├── pivots.py          Swing high/low detection (THE FOUNDATION)
│   │   ├── indicators.py      RSI, ATR, BB, MACD, ADX, SMA/EMA, volume ratio
│   │   ├── patterns.py        3 detectors: 52w_high / Darvas / NR7 (+ 6 more in Phase 2)
│   │   └── stage.py           Weinstein 1/2/3/4 classifier
│   │
│   ├── filters/
│   │   └── quality.py         Market cap, ADV, listing age, promoter pledge
│   │
│   ├── paper/
│   │   └── tracker.py         Paper-trade state machine + position sizing
│   │
│   ├── output/
│   │   └── alerts.py          Alert dataclass + CSV channel + Telegram stub
│   │
│   └── jobs/
│       ├── morning_scan.py    9:30 AM IST — populate setup watchlist
│       ├── preclose_scan.py   3:00 PM IST — confirm breakouts, emit alerts
│       └── eod_settle.py      4:00 PM IST — walk paper trades through state machine
│
├── tests/                     Pytest suite — 78 tests
│   ├── test_pivots.py         27 tests on the pivot foundation
│   ├── test_indicators.py     20 tests on each indicator
│   ├── test_patterns.py       23 tests (3+ positive / 3+ negative per pattern)
│   ├── test_stage.py          7 tests on the 4 Weinstein stages
│   └── test_end_to_end.py     1 integration test: setup → alert → paper trade → settle
│
├── data_cache/                Excel workbook + cached OHLCV (gitignored, created on first run)
│   └── breakout.xlsx
├── logs/                      Per-day log files (gitignored)
│   └── YYYY-MM-DD.log
├── output/                    CSV alert files (gitignored)
│   └── alerts_YYYY-MM-DD.csv
└── .venv/                     Python virtual environment (gitignored)
```

### What each module does, in one sentence

| File | One-sentence summary |
|---|---|
| `breakout/config.py` | Reads `config.yaml` + `.env`, returns a frozen typed `Config` object the rest of the codebase accesses by attribute. |
| `breakout/logging_setup.py` | Configures Python's logging to write DEBUG to `logs/YYYY-MM-DD.log` and INFO to a colored rich console. |
| `breakout/scoring.py` | `composite_score(features)` — 0–100 score from quality/stage/pattern/volume/RS/tightness/sector inputs. Returns 0 if any hard gate fails. |
| `breakout/data/store.py` | `Store(workbook_path)` — every read/write to the Excel workbook goes through here. Use as a context manager (flushes on exit). `upsert_prices` also keeps the cache on a single adjustment basis: when the feed re-adjusts a symbol it scales the cached bars, and the levels on that symbol's open paper trades, by the measured factor. |
| `breakout/data/universe.py` | `refresh_universe_if_stale(store)` — downloads NIFTY 500 CSV from NSE if cache is >7 days old. |
| `breakout/data/fetcher.py` | `make_fetcher(cfg)` returns YFinance or AngelOne based on config + credential availability. Both implement `fetch_history(symbol, days)` and `fetch_history_batch(symbols, days)`. Prefer the batch call in loops — YFinance overrides it to fetch ~50 tickers per HTTP request (8x faster over a full universe). **Angel One is disabled as of 2026-09** — its `getCandleData` returns AB1021 "Too many requests" even at 8s spacing (server-side bug on their end, see the fetcher docstring). |
| `breakout/data/validate.py` | `is_continuous(df, symbol)` — rejects a price series containing an implausible one-day move. Catches demergers and capital reductions, which Yahoo does **not** adjust for (splits and bonuses it does handle). All three jobs skip a symbol that fails. |
| `breakout/analysis/pivots.py` | `find_pivots(df, n=5)` — confirmed swing highs and swing lows. Every pattern depends on this being right. |
| `breakout/analysis/indicators.py` | Pure functions: `sma`, `ema`, `rsi`, `atr`, `macd`, `bollinger_bands`, `adx`, `volume_ratio`. Plus `add_standard_indicators(df)` attaches all of them as columns. |
| `breakout/analysis/patterns.py` | `detect_fifty_two_week_high_breakout`, `detect_darvas_box`, `detect_nr7`. Plus `detect_all(df, enabled)` runs the enabled set and returns matches sorted by confidence. |
| `breakout/analysis/stage.py` | `classify_stage(df)` returns `Stage.STAGE_1` through `STAGE_4` or `UNKNOWN`. |
| `breakout/filters/quality.py` | `check_quality(df, metadata, thresholds)` — hard gate; returns `QualityResult(passed, reasons_failed)`. |
| `breakout/paper/tracker.py` | `position_size`, `compute_stop`, `compute_targets`, `insert_alert`, `settle_one_trade`, `apply_outcome`. The full paper-trade lifecycle. |
| `breakout/output/alerts.py` | `Alert` dataclass, `CSVChannel`, `build_channels(cfg)`, `dispatch_alerts(alerts, channels)`. |
| `breakout/jobs/morning_scan.py` | Entry point: refresh universe → fetch prices → analyze each → populate `setup_watchlist`. |
| `breakout/jobs/preclose_scan.py` | Entry point: re-fetch today's data for watchlist stocks → confirm breakouts → emit alerts + insert paper trades. |
| `breakout/jobs/eod_settle.py` | Entry point: walk every open paper trade through the state machine using today's OHLC. |

---

## 6. Indicators: what they measure and why

All indicators in `breakout/analysis/indicators.py`. Pure functions; nothing has hidden state.

### SMA (Simple Moving Average)

`sma(series, period)` — average of the last N values.

**Why we use it:** The 30-week (150-day) SMA is the backbone of Weinstein staging. Below it = warning; above it with rising slope = Stage 2. We also compute 50-day and 200-day SMAs for context (the classic "golden cross" / "death cross" reference points).

### EMA (Exponential Moving Average)

`ema(series, period)` — weighted moving average where recent values count more.

**Why:** Used by MACD and several Phase 2 patterns. Reacts faster to changes than SMA; useful for short-term momentum.

### RSI (Relative Strength Index)

`rsi(close, period=14)` — 0 to 100 oscillator measuring recent gains vs recent losses.

**Why:** Not central to Phase 1 alerts but logged for context. >70 = overbought, <30 = oversold (rough rules; tune per stock). Useful for filtering "buying the top" — if RSI is 90 on a breakout, the move may be exhausted.

### ATR (Average True Range)

`atr(df, period=14)` — average size of the daily price range, accounting for gaps.

**Why this is critical:**
1. **Stop-loss sizing.** We set stops at 1.5 × ATR below the breakout level. This adapts to the stock's natural volatility — a Rs 50 ATR stock gets a wider stop than a Rs 5 ATR stock, so the *probability* of being stopped on noise is consistent across positions.
2. **Tightness signal** (Phase 2). Comparing recent ATR(10) to longer-term ATR(50) measures volatility contraction — a key pre-breakout signal.

Implementation uses Wilder's smoothing (the standard for ATR, matches TradingView and Chartink).

### MACD (Moving Average Convergence Divergence)

`macd(close, 12, 26, 9)` — momentum oscillator combining short and long EMAs.

**Why:** Logged but not used in Phase 1 alerts. Available for Phase 2 confirmations or custom analysis.

### Bollinger Bands

`bollinger_bands(close, 20, 2.0)` — 20-day SMA ± 2 standard deviations.

**Why:**
- The **BB Squeeze** pattern (Phase 2) fires when band width hits a 6-month minimum — extreme volatility contraction precedes expansion.
- `bollinger_band_width(...)` normalizes width by middle band so it's comparable across price levels.

### ADX (Average Directional Index)

`adx(df, period=14)` — measures trend strength regardless of direction. >25 = strong trend.

**Why:** Logged for context. Used in Phase 2 to filter out range-bound stocks for trend-following patterns.

### Partial sessions and the intraday volume profile

Both scans compare *today's* volume against a 20-day average of *complete* days. While today's bar is still being written that is apples-to-oranges, and biased one way only — the partial bar is always too small, so the 1.5× gate is harder to clear than configured and the volume score reads low.

The obvious correction, scaling by elapsed clock time, is wrong. NSE volume is U-shaped: heavy at the open, heavy into the close, thin through the middle. Measured over **276 symbol-days of 5-minute bars across 12 large NSE names** (2026-09-12):

| Time | Measured | Clock time |
|---|---|---|
| 09:30 | 0.039 | 0.040 |
| 10:30 | 0.181 | 0.200 |
| 11:30 | 0.316 | 0.360 |
| 12:30 | 0.471 | 0.520 |
| 13:30 | 0.609 | 0.680 |
| 14:30 | 0.747 | 0.840 |
| **15:00** | **0.851** | **0.920** |
| 15:30 | 1.000 | 1.000 |

The closing half hour carries ~15% of the day's volume in 8% of its minutes, and clock time overstates progress all session long. At 3 PM it claims 92% when the truth is 85% — so **a genuine 1.5× volume day read as 1.27× and failed the gate.**

The two scans need opposite treatments, because the same correction is not safe at both ends:

- **Pre-close (3:00 PM, ~85% done)** — project today's volume to a full-day estimate by dividing by the profile fraction. 85% is a stable base, and the projected figure ("tracking toward 2.1× average") is the one a trader would reason about. After the close, or on a settled bar, this is a no-op.
- **Morning (9:30 AM, ~4% done)** — **drop the partial bar entirely**. Projecting from 4% multiplies whatever the opening fifteen minutes happened to do by ~26, which amplifies noise rather than removing bias. Left in, every candidate's volume ratio read as ~0.04× and its score was dragged down for no reason but the clock. The morning scan judges setups on completed history; the pre-close scan does the real volume check.

`MIN_PROJECTABLE_FRACTION` (0.5) is the cutoff between the two behaviours.

These are medians over liquid large caps. A thin name's profile will differ and the numbers will drift with market structure — they're a far better estimate than the implicit 1.00 the code used before, not a precise constant. `analysis/session.py` also pins the session clock to **Asia/Kolkata** regardless of the machine's timezone, which is a down payment on the broader timezone item (`IMPROVEMENTS.md` #13).

### Volume Ratio

`volume_ratio(volume, period=20)` — today's volume divided by the 20-day average.

**Why this is the second-most-important indicator after stage:**
A breakout candle is meaningless without volume confirmation. Institutional buying shows up in the *volume*. The 1.5× threshold (`min_volume_ratio` in config) is conservative; some traders use 2× or 3×.

Both `volume_ratio_20` and `vol_avg_20` get added to the DataFrame by `add_standard_indicators()`.

---

## 7. Pivots: the foundation of everything

`breakout/analysis/pivots.py`. The most important file in the project. **Every chart pattern depends on this being correct.** If pivots are wrong, every pattern detector downstream produces garbage.

### What is a pivot?

A **swing high** at bar `i` is a bar whose `high` is strictly greater than the highs of all `N` bars on either side. Default `N=5`, meaning the bar needs to dominate 5 bars to the left and 5 bars to the right.

A **swing low** is the mirror: `low[i]` is strictly less than all neighbors within N bars.

### Why N = 5

For daily swing trading, 5 bars on each side is the sweet spot:
- **N=3** detects too many minor wiggles. Noise.
- **N=10** misses meaningful pivots until they're 10 days old. Stale.
- **N=5** balances responsiveness with significance. About one confirmed pivot per 2 weeks of price action.

If you want major-only pivots for longer-term analysis, increase N. If you want faster confirmation, decrease.

### The strictness rule

We use *strict* dominance — the pivot must be the *unique* maximum (or minimum) in the window. Two bars at the same high cancel each other out; neither is a pivot.

**Why strict:** A flat top with two bars at identical highs is ambiguous resistance. We'd rather miss a borderline pivot than have a noisy signal feed the pattern detectors. Pattern detection is fragile to bad inputs; safer to over-filter at the pivot stage.

### The confirmation lag

A pivot at bar `i` requires `N` future bars to confirm. **The last N bars in any series cannot yet be classified as pivots.** They might turn out to be pivots once enough future bars arrive, but right now we don't know.

Pattern detectors must account for this lag. The 52w-high breakout detector, for example, can check the high WAS broken but the breakout-day pivot can't be confirmed until N days later. That's why the *level* (the 52-week high price) is what we use, not "the latest swing high."

### Strength

Each confirmed pivot gets a strength score: how many bars on each side it strictly dominates *looking beyond the N=5 confirmation window*. A pivot with strength 30 dominated 30 bars to the left and 30 to the right — a major peak. A pivot with strength 5 is just barely confirmed.

We report `strength = min(left_run, right_run)` — the conservative measure. A pivot that dominates 30 left and 5 right is really a "5-bar pivot" in terms of resistance quality.

### Validation

27 tests in `tests/test_pivots.py` cover:
- Empty / too-short DataFrames
- Strict pivot detection (rejecting flat tops / flat bottoms)
- First/last N bars correctly excluded
- Strength calculation under asymmetry
- Larger N filters minor swings
- Chronological order guarantee (downstream code assumes ascending indices)

You manually verified the last 5 RELIANCE pivots against TradingView — that's the gold-standard check.

---

## 8. Pattern detectors

`breakout/analysis/patterns.py`. Each detector is a pure function: takes a DataFrame, returns a `PatternMatch` dataclass.

```python
@dataclass
class PatternMatch:
    detected: bool          # False = no match; ignore other fields
    pattern_name: str
    confidence: float       # 0-100, how cleanly the structure fits
    breakout_level: float   # the price needed for a confirmed breakout
    base_start_idx: int     # where the consolidation/base begins
    base_end_idx: int       # where it ends (usually = today)
    base_height: float      # for the measured-move target
    notes: dict             # pattern-specific extras
```

### Phase 1: three detectors

#### 1. `detect_fifty_two_week_high_breakout`

**What it looks for:** Today's close is within 2% of the 252-day (≈ 1 year) high.

**Why this pattern:** A stock approaching its 52-week high after a long base is the cleanest possible setup. The level is psychologically meaningful (institutional desks watch this number), the resistance has been tested by definition, and a break confirms a new high in long-term momentum.

**Confidence formula:** `min(100, 50 + (touches - 1) × 5)`. Each prior approach to the level (within 1.5% in the last 6 months) adds 5 points. A stock that's tested the level 10 times has very flat resistance and a much higher chance of clean follow-through when it finally breaks.

**Why chosen over alternatives:**
- *All-time high breakout* — would be cleaner but most NSE stocks haven't been listed long enough. 52-week is the next-best.
- *N-day high breakout (e.g., 20-day)* — too noisy. Many false signals from short-term swings.

#### 2. `detect_darvas_box`

**What it looks for:** A 20–80 day window where:
- All highs cluster within 3% of a ceiling
- All lows cluster within 5% of a floor
- The ceiling is touched at least 3 times

Returns the longest qualifying box ending today.

**Why this pattern:** Named after Nicolas Darvas (1950s trader). It's a tight consolidation after an advance — the stock has run up, then traded sideways while institutions accumulate, then breaks out on the next leg. The clear definition (ceiling + floor + touches) makes it mechanically detectable without overfitting.

**Confidence formula:** `60 (baseline) + extra touches × 5 (max +20) + tightness bonus (+10 if box width < 8% of ceiling, +5 if < 12%, else 0)`. Tighter boxes break out more reliably; more touches = more confirmed resistance.

**Why chosen over alternatives:**
- *Donchian channels* — similar but doesn't require flat top/bottom. Less selective.
- *Rectangle pattern* — same idea, no formal definition. Hard to detect mechanically.

#### 3. `detect_nr7`

**What it looks for:** Today's true range is the smallest of the last 7 days.

**Why this pattern:** Tony Crabel popularized NR7 in the 1990s. The intuition is volatility-cycles: extreme contraction (today's tight range) precedes extreme expansion (tomorrow's directional move). Statistically, an NR7 bar's range is followed by an above-average range bar in 60–70% of cases.

**Confidence formula:** `50 (baseline) + 30 if near a recent swing high + 20 if volume contraction`. The most powerful version is "NR7 at resistance with declining volume" — the compression-before-expansion pattern.

**Why chosen over alternatives:**
- *Inside bar* — similar concept but only checks one prior day. Phase 2 will add inside-bar separately.
- *NR4 / NR10* — variants. NR7 is the original and most documented.

### Phase 2: six more detectors

| Pattern | What it looks for | Why |
|---|---|---|
| Inside bar | Today's bar entirely contained within yesterday's | Quick compression signal; often combined with NR7 |
| Bollinger Band squeeze | BB width at 6-month minimum, price in upper half | Volatility cycle: extreme low = expansion imminent |
| Ascending triangle | Flat top + rising-slope bottom trendline | Classic continuation pattern; very directional bias |
| VCP (Volatility Contraction Pattern) | 3+ pullbacks, each shallower than the last | Minervini's signature setup; explicit institutional accumulation footprint |
| Cup & Handle | U-shape base + shallow handle | O'Neil's CAN SLIM pattern; multi-month base = high conviction |
| Flag | Sharp move + tight counter-trend channel | Continuation after a strong leg; very high success rate when clean |

All Phase 2 patterns are documented in the spec (`BUILD_PROMPT.md`) with their exact detection rules.

### Dispatcher

`detect_all(df, enabled_list)` runs every enabled detector and returns matches sorted by confidence descending. The strongest pattern leads — useful when multiple patterns fire on the same stock.

---

## 9. Stage classification (Stan Weinstein)

`breakout/analysis/stage.py`. The hardest filter — eliminates ~70% of false breakouts by itself.

### The four stages

| Stage | Name | Price vs SMA | SMA slope | Trade direction |
|---|---|---|---|---|
| 1 | Accumulation | Around flat SMA, after a Stage 4 | Flat | Don't trade |
| 2 | Advance | Above rising SMA | Rising | **Long breakouts** ← this is where we trade |
| 3 | Distribution | Around flat SMA, after a Stage 2 | Flat | Exit longs |
| 4 | Decline | Below falling SMA | Falling | Don't trade (or short if you do that) |

### How we classify

The classifier looks at three signals:
1. **Where is close vs the 30-week (150-day) SMA?** Above or below.
2. **What's the SMA's slope over the last 10 weeks?** Rising, flat, or falling.
3. **Are the last 10 weeks' highs higher than the prior 10 weeks'?** Higher-highs check.

| Conditions | Returns |
|---|---|
| Above SMA + rising SMA + higher highs | `STAGE_2` |
| Below SMA + falling SMA | `STAGE_4` |
| Flat SMA, was previously declining | `STAGE_1` |
| Flat SMA, was previously rising | `STAGE_3` |
| Insufficient data (< 200 bars) | `UNKNOWN` |
| Anything else | `STAGE_3` if above SMA else `STAGE_4` (conservative — non-Stage-2) |

### The slope threshold

"Rising" means slope > 0.0005 per bar in relative terms. That's about 12.5% annualized SMA growth — a meaningful trend.

**Why 0.0005:**
- 0.001 (≈25%/year) — too strict; misses moderate uptrends.
- 0.0001 (≈2.5%/year) — too loose; barely-drifting stocks qualify.
- 0.0005 — sweet spot. Real Stage 2 stocks easily clear this; weak drifts don't.

This threshold is in `breakout/analysis/stage.py:43` as `_FLAT_SLOPE_BAND`. Tunable if you want to be more or less inclusive.

### Why only trade Stage 2

Weinstein's empirical observation: stocks in Stage 2 make the majority of their gains. Stages 1 and 3 are sideways noise. Stage 4 is destruction. The historical edge of a Stage-2-only filter is one of the most consistently documented results in technical analysis.

Concretely: in our test runs with NIFTY large-caps in the current correction, only TITAN qualified as Stage 2. The system correctly refused to alert on TCS, which had a clean NR7 pattern but was in Stage 4. **That's the system saving you from a losing trade.**

---

## 10. Gates and filters

### Which gates actually do anything (`breakout/filters/gate_audit.py`)

Read this before trusting the list below. Several of the hard filters pass every symbol, and until 2026-09-12 nothing said so — they read as active in `config.yaml` and in this guide. **A gate that quietly approves everything is worse than no gate: it reads as protection you do not have.**

The morning scan now logs a gate audit every run, classifying each filter:

| State | Meaning |
|---|---|
| `ACTIVE` | can and does reject symbols |
| `INERT` | configured, but has no data to act on — a missing source, not a design choice |
| `REDUNDANT` | works, but something upstream already guarantees it; it can never be the reason a symbol is rejected |

As measured on 2026-09-12 against the live universe:

```
ACTIVE    adv                rejected 4/500 (0.8%) below 5cr
REDUNDANT market_cap         NIFTY 500 membership already implies far more than 500cr
REDUNDANT listing_age        the 200-bar history requirement already excludes new listings
INERT     promoter_pledge    no source wired — passes every symbol
INERT     earnings_blackout  0/58 symbols returned any earnings date
-> 2 gate(s) INERT. These pass every symbol; treat the protection they imply as absent.
```

The distinction between INERT and REDUNDANT is the useful part:

- **`market_cap` is redundant, not broken.** The 15 lowest-turnover NIFTY 500 names have market caps from **₹10,052 cr** up — twenty times the ₹500 cr floor. Wiring a market-cap source would be pure cost for zero effect. Nothing to fix.
- **`promoter_pledge` is genuinely inert, and it matters.** Unlike market cap, it *would* reject real names if connected — index membership does not screen for pledging. There is no free reliable feed (NSE publishes shareholding patterns as quarterly filings, not an API), so this is a real, unpatched hole.
- **`earnings_blackout` is inert** because yfinance has no NSE earnings coverage. Needs an Indian source.
- **`adv` works but barely binds.** Median ADV across the universe is ₹83 cr against a ₹5 cr floor, and the thinnest constituent trades ₹3.3 cr/day — over 100× the largest position the capital cap permits. It's a cheap safety rail against a thin name entering the index, not a liquidity constraint — keep it, but don't imagine it's protecting your fills.

### Quality floor (`breakout/filters/quality.py`)

Hard gate: a stock either passes or fails. Reasons for failure:
- **`adv_below_floor`** — average daily turnover < ₹5 cr over last 20 days. Illiquid.
- **`market_cap_below_floor`** — market cap < ₹500 cr. Penny / microcap territory.
- **`too_recent_listing`** — listed < 365 days. Not enough chart history.
- **`high_promoter_pledge`** — promoter pledge ≥ 30%. (Phase 2 — currently default-pass because there's no free clean data source for this yet.)

ADV is computed directly from the price/volume data, no metadata needed. The other three depend on the universe table having those fields populated (NSE's CSV doesn't include market cap, so it's None for now — that check default-passes too in Phase 1).

### Earnings blackout (Phase 2)

Exclude stocks within ±5 trading days of an earnings announcement. Patterns get disrupted by news events; breakouts on earnings day are not the breakouts this strategy is built for. Default-pass in Phase 1 (no source wired yet).

### Data freshness

A symbol whose fetch failed keeps whatever is already in the cache. The morning scan used to decide *which* symbols to fetch before fetching them and then scan the whole universe regardless — so a failed fetch meant a symbol was analysed against prices days old, and could emit a breakout "signal" from them.

Freshness is now re-checked **after** the fetch, and stale symbols are excluded from both the scan and the RS cross-section. The cross-section matters as much as the scan: a symbol frozen at last week's price carries a stale 63-day return, which distorts every *other* symbol's RS percentile.

The subtlety is what "fresh" means, and it differs by job:

| Job | Requirement | Why |
|---|---|---|
| `morning_scan` (9:30) | latest bar ≥ **last completed session** | Today's bar is still being written and is deliberately excluded (§ 6). Requiring "a bar for today" here would reject the entire universe. |
| `preclose_scan` (3:00) | latest bar **is today** | It is asking "did this break out *today*" — yesterday's close cannot answer that, and confirming against it would open a position on a move that may already be over. |
| `eod_settle` (4:00) | latest bar **is today** | Already enforced; settling against a stale bar would fabricate transitions. |

`trading_calendar.last_completed_session()` is the yardstick: today once the close has passed, otherwise the previous trading day, skipping weekends and holidays. It reads the clock in **Asia/Kolkata**, so a scheduler running in another timezone can't mislabel yesterday's bar as today's.

Measured against the live cache on 2026-09-12: 493 of 500 symbols fresh, 7 excluded (frozen a day behind after failed fetches) — those seven are precisely the ones that were previously being scanned against old prices.

### Price continuity (`breakout/data/validate.py`)

Hard gate, applied in all three jobs before anything else looks at the bars. A
series containing an implausible one-day close-to-close move (below −25% or
above +40%) is skipped entirely.

The target is **demergers and capital reductions**. yfinance back-adjusts splits
and bonuses for us, but Yahoo has no split record for a demerger, so the price
drop stays in the series looking like a day's trading. On the live NIFTY 500
cache that hit VEDL (−65%), ABFRL (−67%), TMPV (−40%), TRENT (−33%) and HEG
(−63%) — five symbols whose 52-week high, base structure, ATR-derived stop, SMA
slope and RS return were all being computed across a price cliff. Refetching
does not clear them; only excluding them does.

The band deliberately over-rejects: a genuine crash (INDUSINDBK −27% in March
2025, IEX −30% on market-coupling news) trips it too, and no threshold can
separate the two because demerger ratios are arbitrary. That costs nothing real
— a stock that just fell 27% in a day will not pass the Stage 2 gate for months
— and the exclusion lapses on its own once the bar ages out of the 300-day
window.

In the morning scan the check runs in the RS pre-pass, not just the main loop:
a −65% phantom return would otherwise drag the percentile of **every other**
symbol in the cross-section.

### Market mood

India VIX is logged per alert (`vix`, `market_mood`) and feeds one of the three regime votes below. On its own it is advisory — the morning scan warns when VIX is elevated but does not block.

### Market regime (`breakout/filters/regime.py`)

Breakout strategies concentrate nearly all their losses in risk-off regimes — the same setup that works in a broad advance fails repeatedly when the index is below a falling 200DMA and participation is narrowing. Three independent reads of "is the market paying for breakouts right now?", each voting −1 / 0 / +1:

| Vote | Source | +1 | −1 |
|---|---|---|---|
| `nifty` | ^NSEI close vs its 200DMA | above a rising 200DMA | below a falling one |
| `breadth` | % of universe above its own 50DMA | ≥ 60% | ≤ 40% |
| `vix` | India VIX vs its 20-day average | `risk_on` | `risk_off` |

The sum lands in [−3, +3]: **≥ +2 risk_on**, **≤ −2 risk_off**, else neutral. Breadth is free — the morning scan already reads every symbol's history for the RS pre-pass, so counting 50DMA positions costs nothing extra.

**What it does:** scales `risk_per_trade_pct` at entry. Risk-off → 0.5×, all-three-negative → 0.25×, everything else → 1.0×. It never raises size above baseline — there's no evidence yet that risk-on days deserve more, and inventing upside is how a filter becomes a leverage knob.

**Why a multiplier and not an on/off switch** — two reasons, and the first is the important one:

1. A hard gate discards exactly the observations that would tell you whether the gate was right. Sizing down keeps the alert, the paper trade and the `alert_features` row, so `regime`, `regime_score` and `breadth_pct` accumulate against realised `pnl_r` and the thresholds can later be set from results rather than from the priors they are today. `python -m breakout.paper.stats` buckets them.
2. Regime is a continuum. Half size in a deteriorating tape models what a discretionary trader actually does better than a binary halt.

Sizing is all it touches: stop, targets and the R:R they imply are properties of the setup, not of the tape. A half-size trade that stops out is still exactly −1R, it just costs half as many rupees. Reduced-size entries are marked `risk_x0.50` in the trade's `notes`.

The assessment runs once per morning (breadth needs the whole universe) and rides on every `setup_watchlist` row; the pre-close scan reads it back rather than recomputing. Rows written before this existed size at 1.0, as does a failed ^NSEI fetch — a data gap is not evidence of a bad tape.

Set `regime.enabled: false` to keep measuring the regime without acting on it.

> The backtest does **not** apply this gate — it would need point-in-time breadth across the universe. See `IMPROVEMENTS.md` § 11 for why the backtest is a smoke test rather than an edge estimate.

---

## 11. Composite scoring

`breakout/scoring.py`. Combines hard gates and weighted contributions.

```python
def composite_score(features: ScoringFeatures) -> float:
    if not features.quality_pass:               return 0   # hard gate
    if features.stage != Stage.STAGE_2:          return 0   # hard gate
    if features.earnings_blackout:               return 0   # hard gate (Phase 2)
    if features.pattern_match is None:           return 0   # hard gate
    
    return (
        features.pattern_match.confidence * 0.35      # max 35
        + 15                                          # Stage 2 (gated above)
        + min(15, features.rs_score)                  # max 15
        + volume_points(features.volume_ratio)        # max 15 — see below
        + close_in_range_points(features.close_in_range)   # max 5 — see below
        + min(10, features.tightness_score * 10)      # max 10
        + {"up": 5, "flat": 3, "down": 0}.get(features.sector_trend, 3)  # max 5
        - extension_penalty(features.extension_pct)   # max 10 off — see below
    )   # floored at 0
```

### Why these weights

- **Pattern quality 35%** — biggest contributor. A confident pattern (e.g., a clean Darvas with 6 touches) is much more reliable than a barely-detected one.
- **Stage 15%** — already a hard gate; the points reward you for being in the right environment. Was 20; 5 points moved to close-in-range below, because a constant awarded to every candidate that passes a gate carries no information.
- **Relative strength 15%** — a stock outperforming the market is in demand. Often the best Stage-2 advances come from RS leaders.
- **Volume 15%** — confirms institutional participation. Without it, even a clean pattern is "retail interest" only.
  
  The scale is piecewise-linear with a knee at the confirmation gate: **0× → 0 pts, 1.5× → 7.5 pts, 3.0× → 15 pts**, capped beyond. Half weight at the gate rather than zero, because clearing the gate is itself evidence; capped at 3× because a 10× spike is usually news, not accumulation. Continuous at the knee, so a candidate crossing 1.5× doesn't lurch in the ranking, and still ranked below it so the morning watchlist (where nothing has broken out yet) can order candidates. `thresholds.volume_saturation_ratio` tunes the top end.
- **Tightness 10%** — pre-breakout compression. Important but secondary; many patterns work without it.
- **Close-in-range 5%** — where the confirmation bar closed within its own high-low range (0 = at the low, 1 = at the high). This is the direct measurement of the thesis in § 2: *institutional commitment shows in the close*. `close > level` treats a breakout that finished on its high identically to one that spent the day at the level and closed near its low having given back everything it gained.

  Unknown scores the **full** 5, not zero and not a midpoint: the morning scan has no breakout bar to judge, and anything else would shift every watchlist score for no informational reason. So a strong close scores exactly what it did before this component existed, and only a weak close gives ground.
- **Sector 5%** — small modifier. A rising sector helps but isn't decisive.
- **Chase penalty, up to −10** — the only negative term. By 3 PM a breakout has usually already run past its pivot, and how far matters: the stop is anchored to the *level*, so a wider extension means wider risk per share and a smaller position, and `target_2` is a measured move from the level, so extension eats directly into reachable upside. Free up to 2%, ramping to the full −10 at 8%, capped beyond (a 40% extension shouldn't swamp everything else). Entering *below* the level — the pullback path — is never penalised, and neither is an unknown extension: the morning scan has no entry yet, and absence of data isn't evidence of a chase.

  A scored penalty rather than a hard "reject above 5%", for the same reason the regime gate sizes down rather than blocking: a rejected alert leaves no `alert_features` row, so a hard cut would destroy the evidence needed to find the real cut point. In practice the penalty plus `min_score_to_alert` acts as a soft gate — a badly extended entry simply stops clearing the bar. The score is floored at 0, since 0 is reserved for "failed a hard gate"; a chased entry is a weak setup, not a disqualified one.

### Thresholds

| Threshold | Where it's enforced | Default |
|---|---|---|
| Watchlist threshold | `morning_scan` — anything ≥ this goes on `setup_watchlist` | 50 |
| Alert threshold | `preclose_scan` — must score ≥ this *and* confirm breakout to alert | 60 (`min_score_to_alert` in config) |

The 10-point buffer between watchlist (50) and alert (60) accounts for the volume boost that comes on actual breakout day. A stock can sit at 55 on the watchlist (moderate volume), then climb as today's volume spikes — now genuinely so: under the widened volume scale, going from 1.0× to 3.0× is worth +10 points, where before the two scored within 5 of each other and anything ≥1.5× was identical.

The pre-close rescore calls `composite_score` again over the features the
morning stored, with the confirmation day's volume substituted in. It used to
patch the score arithmetically (add the new volume term, subtract an assumed-1.0
one), which was close but drifted from the scorer whenever a weight changed.

### Known limits of these weights

Worth knowing before you trust the number:

- **Stage contributes no discrimination.** It's a hard gate, so every scored
  candidate gets exactly 15 (was 20 — the other 5 became close-in-range, which
  does discriminate). It still shifts all scores up by a constant; `60` is
  really "≈45 points of varying signal".
- **`atr_pct` is logged but deliberately still unscored.** Unlike
  close-in-range it has no agreed direction — high volatility is either the
  fuel for a fast move or the noise that shakes you out, and which one depends
  on the name. It is also already priced in twice over: the stop is
  `1.5 × ATR` below the level, so a volatile stock automatically gets a wider
  stop and a smaller position. Scoring it today would mean inventing a sign.
  It stays in `alert_features` until the sample says which way it points.
- ~~**Volume saturates at the confirmation gate.**~~ Fixed — the scale now runs
  1.5× → 3.0× (see above). A breakout that only just clears the gate scores
  **7.5 points lower than it used to**, when volume was a free 15 for everyone,
  so the effective floor for a confirmed alert fell from 38 to 30.5 and `60` is
  correspondingly harder to clear. Expect fewer alerts until
  `min_score_to_alert` is re-tuned against results.
- **Ranking is still driven substantially by pattern confidence**, whose
  baselines are detector-specific (52w starts at 50, Darvas at 60, NR7 at 50) —
  i.e. partly by *which* detector fired rather than how good the setup is.
  Volume now competes with it for influence, which it did not before.
- ~~**Extension past the pivot is recorded but unscored.**~~ Fixed — it now
  carries a penalty of up to −10 (see above).

**How much these two fixes changed the number.** For one mid-quality setup
(pattern confidence 60, RS 7.5, tightness 0.5, flat sector), the old score was
**71.5 in every one of these cases** — volume and extension were doing no work
at all:

| vol ratio | extension | old | new | alerts at 60? |
|---|---|---|---|---|
| 1.5× | 1% | 71.5 | 64.0 | yes → yes |
| 1.5× | 5% | 71.5 | 59.0 | yes → **no** |
| 1.5× | 8% | 71.5 | 54.0 | yes → **no** |
| 2.0× | 5% | 71.5 | 61.5 | yes → yes |
| 3.0× | 1% | 71.5 | 71.5 | yes → yes |
| 3.0× | 8% | 71.5 | 61.5 | yes → yes |

Only a 3× breakout taken within 2% of its pivot keeps the old score. Everything
else is now ranked below it, and weak-volume-plus-big-chase drops out
altogether. **Expect meaningfully fewer alerts** until `min_score_to_alert` is
re-tuned against results.

None of these are fixed by re-guessing the weights. `alert_features` plus
`python -m breakout.paper.stats` is the path to settling them with data.

Historical note: in Phase 1, with RS / tightness / sector stubbed to 0, the practical max was Pattern 35 + Stage 20 + Volume 15 + Sector (flat) 3 = **73**, so alerts clustered between 50 and 73. All six signals are live now, and the volume and extension terms are no longer inert, so the working range is much wider — see the before/after table above.

---

## 12. Paper trading

`breakout/paper/tracker.py`. Every alert becomes a virtual position. After 4–6 weeks of running, the `paper_trades` table answers: *what's my actual edge?*

### The backtest is a smoke test, not evidence

`python -m breakout.backtest` prints a win rate, an average R and a profit factor. **Those numbers are not an edge estimate, and sizing real capital on them would be a mistake.** The command prints this warning under its own output every run, so it can't be read without it.

What it is good for: confirming the pipeline still fires and settles trades the way it did last week — a regression check on detector wiring.

Why the numbers can't be trusted as performance:

1. **Survivorship.** It replays *today's* NIFTY 500 over history, so every name in the sample survived and stayed in the index; the ones that collapsed out of it are absent. Inflates results, and not cheaply fixable — historical index membership isn't freely available (`IMPROVEMENTS.md` #15).
2. **Non-overlapping trades.** After a trade closes the scan resumes past its exit, discarding any signal that fired while a position was held. The sample is biased toward periods following quick exits.
3. **No RS or sector.** Both need the whole universe as of each historical day, so they score 0 and 'flat'. Backtest scores therefore sit *below* live scores — a setup that clears `min_score_to_alert` live may not clear it here.
4. **No regime gate.** Needs point-in-time breadth across the universe.
5. **No concentration limits.** Every signal is taken; the live scanner caps total and per-sector positions and would not have taken them all.

Costs, gap-through-stop fills and the capital cap on position size *are* modelled and match the live tracker exactly.

**The forward paper-trade log is the edge estimate.** That's what § 12 is about, and it's the only measurement here made in the current regime with the current logic.

### Why paper trade

Backtest results are not predictive — Indian market regimes shift fast (election cycles, RBI policy, FII flows). What worked in 2023 may not work in 2026. The only reliable edge estimate is forward paper trading in the *current* regime, with the *current* logic.

4–6 weeks is roughly enough to log 20–50 paper trades. That's a small but meaningful sample. After 30 trades you can start to see:
- Overall win rate
- Win rate by pattern
- Win rate by score band
- Average R-multiple (R = risk per trade in units)
- Profit factor (gross winners / gross losers)

If win rate is > 50% and average R is > +0.5, the strategy has a real edge in current conditions. Only then should you consider real capital.

### The state machine

```
(pre-close scan confirms a breakout) ──→ ENTERED
   │        opened at the 3 PM price, immediately — there is no waiting state

ENTERED
   │
   ├── (today's low ≤ stop_loss) ──→ STOPPED_OUT
   ├── (today's high ≥ target_2) ──→ TARGET_HIT
   ├── (today's high ≥ target_1, but not target_2) ──→ TARGET_1_HIT
   └── (days_held ≥ hold_max_days) ──→ TIME_EXIT

TARGET_1_HIT  (partial — stop now at breakeven)
   │
   ├── (today's low ≤ entry_price) ──→ STOPPED_OUT  (breakeven; net flat)
   ├── (today's high ≥ target_2) ──→ TARGET_HIT
   └── (days_held ≥ hold_max_days) ──→ TIME_EXIT
```

`ALERTED` and `CANCELED` are legacy states from the old next-day-open entry model. Nothing produces them any more; existing rows in `ALERTED` are skipped by the settle rather than crashing it.

### Position sizing

`position_size(capital, risk_pct, entry, stop)`:

```
risk_amount = capital × (risk_pct / 100)
risk_per_share = entry - stop
shares = floor(risk_amount / risk_per_share)
```

For your config: ₹2,00,000 × 2.5% = ₹5,000 risk per trade. If a stock has entry ₹500 and stop ₹485 (risk ₹15/share), you'd take 5000 / 15 ≈ 333 shares.

This is **risk-based sizing**, not equal-rupee sizing. Every position risks the same amount if the stop hits — wider stops = fewer shares, tighter stops = more shares. The key property is that your maximum loss per trade is consistent regardless of the stock's price or volatility.

#### The capital cap — and why risk sizing alone is dangerous

Look again at that example: 333 shares at ₹500 is **₹1,66,500**, or 83% of the whole account, in one position. That is not a quirk of the numbers chosen. Risk-based sizing knows what a position can *lose*, not what it *costs*, and the identity is unforgiving:

```
position value = risk_amount / (stop distance as a fraction of price)
```

A 2.5% stop with 2.5% capital risk buys **exactly 100% of the account** in a single trade. Anything tighter buys more than the account holds.

This was live until 2026-09-12. Measured across 94 real setups from cached prices:

| | |
|---|---|
| median position | **85% of capital** |
| mean | 96.5% |
| exceeded 100% of capital | **30 of 94** |
| exceeded 25% of capital | **94 of 94** |
| largest | **₹6,84,607** on a ₹2,00,000 account (342%) |

The worst case was a GRASIM entry at ₹3,140 with the stop ₹23 below it — a 0.7% stop, so risk sizing bought 218 shares. The share counts the scanner printed in alerts were not ones the account could have paid for, and `insert_alert` used the same function, so the paper log was recording impossible positions.

`position_size()` now takes `max_value`, and `max_position_value(capital, max_concurrent_positions)` supplies it: **capital ÷ slots**, so ₹25,000 at ₹2L and 8 positions, and a full book is exactly 100% of capital. Derived from `max_concurrent_positions` rather than configured separately — "8 concurrent positions" only means something if eight of them fit, and one number can't disagree with itself.

The cap is a ceiling, not a replacement: below it, risk-based sizing still governs, so a wider stop still buys fewer shares.

### Concentration limits

Two caps, both applied at entry in `preclose_scan` via `filters/concentration.py`:

| Limit | Default | What it stops |
|---|---|---|
| `risk.max_concurrent_positions` | 8 | total open positions |
| `risk.max_positions_per_sector` | 3 | open positions sharing a sector |

`max_concurrent_positions` was in `config.yaml` and parsed into `Config` from the beginning, but **had no call site** — nothing read it, so the scanner opened every breakout it confirmed. 2.5% risk per trade across fifteen simultaneous positions is not 2.5% risk.

The sector cap is the more important of the two. Breakouts cluster: a sector move is the single most common reason a batch of setups all confirm on the same day, so without a cap eight positions can be eight pharma names — one bet at 20% of capital wearing the costume of eight bets at 2.5%. For a breakout strategy that is the most common way a good signal becomes a bad month.

When more candidates confirm than there are slots, **the slots go to the highest-scoring candidates**, with ties broken by symbol for determinism. This is why the pre-close scan collects all confirmations first and opens positions in a second pass — processing in watchlist order would hand the day's capacity to whichever symbol happened to sort first.

Positions already open consume both budgets, so limits hold across days rather than resetting each morning. Pullback entries consume a slot like any other entry. Set either limit to `0` to disable it.

Rejections are logged loudly (`N confirmed breakout(s) not taken — at concentration limits, not for lack of quality`). A setup skipped for capacity is not a setup that failed, and if that list is long and frequent then the limits or the capital are wrong — which is worth seeing rather than inferring from a quiet log.

Sector comes from the universe's industry field via `mood.classify_sector`, the same mapping the scoring uses. A symbol whose industry doesn't map to a canonical sector gets its own single-name bucket rather than being pooled under "unknown" — two unclassifiable names are *unknown*, not known to be alike, and pooling them would make them compete for one sector's slots for no reason.

### Stop loss

`compute_stop(breakout_level, atr, multiplier=1.5)`:

```
stop = breakout_level - 1.5 × ATR(14)
```

The 1.5× ATR multiplier is the swing-trade standard. Tighter (1.0×) and you get stopped by normal noise; wider (2.5×) and your risk per share gets too large.

### Targets

Two targets per trade:
- **Target 1** = entry + 2 × (entry - stop). Standard 2:1 reward-to-risk. Hitting this means the position is at +2R.
- **Target 2** = entry + base_height. The measured-move projection — if the breakout extends by the size of the consolidation base, you get this price.

Behavior at target 1: stop moves to entry (breakeven). You can't lose money on the trade anymore. The remaining position runs for target 2.

### Exit fills: gap-through-stop

When the stop is hit, the fill is `min(stop_loss, that day's open)`. If the stock gaps down through the stop overnight, the stop price was never available — the first tradable price is the open, which can be far worse. Assuming a fill at the stop exactly would understate the loss on precisely the trades that hurt most: gap-downs on bad news are the main source of the fat left tail on NSE, so it is where an optimistic assumption does the most damage to the edge estimate.

The same rule applies to the breakeven stop after target 1 — "you can't lose money on the trade anymore" is true intraday, not through a gap.

The target side deliberately has no mirror-image fix: booking the target when the bar opened above it *understates* the gain, which is the conservative direction. `backtest.py::simulate_trade` implements the identical rule, so the two never disagree.

### Transaction costs

Every settled trade — paper and backtest — is netted down by modelled round-trip costs (`breakout/paper/costs.py`, rates in `config.yaml` under `costs:`). NSE equity delivery:

| Charge | Rate | Legs |
|---|---|---|
| Brokerage | lower of 0.1% / ₹20 per order | both |
| STT | 0.1% | both |
| Exchange transaction | 0.00297% | both |
| SEBI turnover | 0.0001% | both |
| Stamp duty | 0.015% | buy only |
| GST | 18% on brokerage + exchange + SEBI | — |
| Slippage | 0.05% (not statutory) | both |

All-in this is roughly 0.25–0.35% of round-trip turnover. On the 3–5% moves this strategy targets that is **5–10% of gross P&L**, and it flips marginal trades negative — a +0.1% move does not pay for itself.

`pnl_inr` and `pnl_r` are both **net**; `gross_pnl_inr` and `costs_inr` are stored alongside so the drag is visible rather than silently baked in, and the digest prints gross, net, and costs-as-%-of-gross. Set `costs.enabled: false` to compare against the old gross-only numbers.

### Time exit

If neither stop nor target hits within `hold_max_days` (default 30 trading days), close at that day's close. The intuition: a breakout that hasn't worked in 6 weeks is probably not going to. Better to free up the capital.

### Tracked metrics per closed trade

- Entry date, entry price
- Exit date, exit price
- PnL in INR and in R-multiples, both **net of transaction costs**
- Gross PnL in INR and the costs deducted, so the drag is auditable
- Days held
- Max favorable excursion — the highest the position ever traded, in R
- Max adverse excursion — the lowest it ever traded, in R

These two were in the schema and documented here from the start but **were never written** until 2026-09-12. They are the cheapest way to learn whether the stop and target are in the right place, and neither question is answerable from realised P&L alone:

- `max_favorable` 3.5R on a trade that exited at the +2R target → the target is too near, you're leaving money on the table
- `max_adverse` −0.9R on a trade that went on to win → the stop is barely surviving; a slightly tighter one would have converted winners into losers

Measured in R rather than rupees so trades of different sizes compare directly. Bars are taken strictly *after* the entry day, matching the settle's rule that the entry bar's range mostly printed before the 3 PM fill. Recomputed from cached bars on every settle rather than accumulated, so re-running the job is idempotent.

---

## 13. The three daily jobs

All three are invokable as `python -m breakout.jobs.<job_name>`. Designed to be scheduled but currently run manually.

### Morning scan — `jobs/morning_scan.py`

**Scheduled time:** 9:30 AM IST (right after market open).

**What it does:**
1. Refresh the NIFTY 500 universe from NSE if cached version is > 7 days old.
2. For each universe symbol, fetch the latest ~300 trading days of OHLCV if not already cached.
3. For each symbol with ≥ 200 bars of history:
   - Apply quality floor → skip if fails
   - Classify Weinstein stage → skip if not Stage 2
   - Run all enabled pattern detectors → skip if no match
   - Check that close is within `near_breakout_pct` (default 2%) of the breakout level → skip if too far
   - Compute composite score → skip if < 50
4. Replace the `setup_watchlist` table with today's surviving candidates.
5. Prune any `pullback_watchlist` entries older than 10 days.

**Output:** Log line `morning_scan complete: N symbols on setup watchlist`. Skip summary breaks down why each rejected symbol failed (insufficient_data / quality / stage / no_pattern / far_from_breakout / low_score).

**Why this design:** The morning scan finds *candidates* — stocks worth watching today. It does *not* emit alerts. Alerts require breakout confirmation, which the pre-close scan handles.

### Pre-close scan — `jobs/preclose_scan.py`

**Scheduled time:** 3:00 PM IST (last 25 minutes of the session).

**What it does:**
1. Read the `setup_watchlist` populated this morning.
2. For each watchlist stock, re-fetch today's latest bar (intraday or close).
3. Check breakout confirmation: `close > breakout_level AND today_volume ≥ 1.5 × 20-day average`.
4. For confirmed breakouts:
   - Compute entry (today's close), stop (1.5× ATR below level), two targets, position size.
   - Emit an `Alert` through every enabled output channel (CSV).
   - Insert a paper trade row with state = `ALERTED`.
   - Add the stock to the `pullback_watchlist` so future retests can be flagged.

**Output:**
- A `rich` table to console showing each confirmed alert.
- `output/alerts_YYYY-MM-DD.csv` with full alert details (or appends if file exists from morning).
- Run log entry in the workbook.

**Why the 3:00 PM timing:** Closing breakouts have a meaningfully higher success rate than intraday breakouts. Stocks that broke out at 11 AM might sell off by 2 PM (false breakout). Waiting until the final 25 minutes filters those out — institutional commitment shows in the close.

### EOD settle — `jobs/eod_settle.py`

**Scheduled time:** 4:00 PM IST (after market close).

**What it does:**
1. Read every paper trade in an OPEN state (`ALERTED`, `ENTERED`, or `TARGET_1_HIT`).
2. For each, fetch today's settled OHLC.
3. Walk through the state machine:
   - `ENTERED` with today's low ≤ stop → close as `STOPPED_OUT`, filled at `min(stop, today's open)` so a gap through the stop is priced honestly.
   - `ENTERED` with today's high ≥ target_2 → close as `TARGET_HIT`.
   - `ENTERED` with today's high ≥ target_1 (but not target_2) → transition to `TARGET_1_HIT` (partial; stop moves to entry).
   - `ENTERED` with days_held ≥ `hold_max_days` (default 30) → close as `TIME_EXIT` at today's close.
   - Legacy `ALERTED` rows are skipped with a warning — nothing produces that state any more.
4. Refresh the derived columns on every open trade: `days_in_trade`, `daily_moves`, and `max_favorable` / `max_adverse`.

**Output:** Log lines showing each transition. Updated `paper_trades` table.

**Why post-close:** The settle uses today's *settled* OHLC. Running it intraday would use partial data and risk premature stop-hits / target-hits based on noise.

---

## 14. Database schema

A single Excel workbook at `data_cache/breakout.xlsx`, one sheet per table.
Just open it in Excel to inspect any sheet. To force a fresh universe fetch,
clear the rows in the `universe` sheet (or delete the whole workbook to rebuild
from scratch).

In the per-table notes below, `PRIMARY KEY` denotes the logical unique key the
`Store` enforces on upsert, and `REAL` / `INTEGER` / `TEXT` describe the intended
value type of each column (Excel cells are untyped). Dates are stored as ISO
strings (`YYYY-MM-DD`).

### Tables (sheets)

#### `prices`
Cached OHLCV. One row per symbol per trading day.
```
(symbol, date) PRIMARY KEY
open, high, low, close: REAL
volume: INTEGER
```

#### `universe`
NIFTY 500 constituent list. Refreshed weekly.
```
symbol PRIMARY KEY
company_name, sector, industry: TEXT
market_cap_cr: REAL  (NULL — NSE CSV doesn't include this; Phase 2 will source)
last_updated: TEXT (ISO date)
```

#### `setup_watchlist`
Today's candidates. Replaced (not appended) every morning scan.
```
symbol PRIMARY KEY
pattern: TEXT  (e.g., 'darvas_box')
breakout_level: REAL
score: REAL  (0-100)
detected_date: TEXT
base_height: REAL  (for measured-move target)
pattern_confidence, rs_percentile, rs_points, tightness, volume_ratio: REAL
distance_pct, adv_cr, vix: REAL
stage, sector, sector_trend, market_mood: TEXT
regime: TEXT              ('risk_on' | 'neutral' | 'risk_off')
regime_score: INTEGER     (-3..+3 — the sum of the three regime votes)
breadth_pct: REAL         (% of universe above its own 50DMA)
nifty_trend: TEXT         ('up' | 'flat' | 'down')
risk_multiplier: REAL     (applied to risk_per_trade_pct at entry)
earnings_blackout: BOOL
notes: TEXT  (compact details: 'adv=12.5cr dist=0.85% touches=4 ...')
```

Each feature is a real column, not just folded into `score`. The pre-close scan
reads them back to recompute the composite with the confirmation day's volume
(`_rescore`) and to write the `alert_features` row. A score alone cannot be
decomposed after the fact.

#### `alert_features`
One row per emitted alert: every signal the scanner saw at the moment it fired.
```
trade_id  -> joins paper_trades.id
symbol, alert_date, alert_type, pattern: TEXT
score, pattern_confidence: REAL
rs_percentile, rs_points, tightness: REAL
volume_ratio: REAL        (confirmation day — the decisive one)
volume_ratio_morning: REAL
sector, sector_trend, stage, market_mood: TEXT
distance_pct: REAL        (morning proximity to the level)
extension_pct: REAL       (how far above the level we actually paid)
close_in_range: REAL      (0 = closed at the low, 1 = at the high)
atr_pct: REAL             (ATR14 as % of entry — volatility, comparable across names)
adv_cr, vix: REAL
regime, nifty_trend: TEXT            (the day's market regime — see § 10)
regime_score, breadth_pct, risk_multiplier: REAL
earnings_blackout: BOOL
breakout_level, entry_price, base_height: REAL
```

**Why this sheet exists.** The composite score weights six signals, but until
now nothing recorded what those signals *were* when an alert fired — only the
final number. That made the weights permanently unimprovable: you could cut
results by score band and pattern, and no further. With this sheet,
`Store.read_alerts_with_outcomes()` joins each predictor to the realised
`pnl_r`, and `paper.stats.feature_report` buckets them, so after a few dozen
settled trades the weights in `scoring.py` can be set from evidence instead of
priors. Upserts on `trade_id`, so re-running a scan the same day is safe.

`close_in_range` and `atr_pct` are new signals that are **logged but not
scored** — deliberately. Log first, score once the sample says it matters.

#### `pullback_watchlist`
Stocks that broke out in the last 10 days and may give a pullback re-entry. Rolling — entries get pruned when older than 10 days.
```
symbol PRIMARY KEY
breakout_date: TEXT
breakout_level: REAL
original_score: REAL
notes: TEXT
```

#### `paper_trades`
The audit log. One row per alert; updated through its lifecycle.
```
id PRIMARY KEY AUTOINCREMENT
symbol, pattern, alert_date, alert_type, score: ...
state: TEXT  (ALERTED / ENTERED / TARGET_1_HIT / TARGET_HIT / STOPPED_OUT / TIME_EXIT / CANCELED)
entry_date, entry_price: ...
stop_loss, target_1, target_2: REAL
exit_date, exit_price: ...
shares: INTEGER
pnl_inr, pnl_r: REAL  (R = R-multiples; both NET of transaction costs)
gross_pnl_inr, costs_inr: REAL  (pre-cost P&L and the drag — see § 12)
days_held: INTEGER
days_in_trade: TEXT  ("D3" — trading days elapsed since entry; entry day = D0)
daily_moves: TEXT    ("D1:1.0%,D2:3.7%,D3:-2.3%" — that single day's move)
max_favorable, max_adverse: REAL
notes: TEXT
```

`days_in_trade` / `daily_moves` are refreshed by `eod_settle` on every run from
the cached bars, so you can see at a glance how a position has behaved since
entry. The day the trade was entered is **D0** — no movement yet, so it carries
no figure. Each later day shows **that day's own move**, not a running total:
D1 is `entry_price` → D1's close, D2 is D1's close → D2's close, and so on.
`D3:-2.3%` therefore means the stock fell 2.3% on day three. Because the whole
string is recomputed rather than appended to, re-running the settle job is
idempotent. To fill the columns in for trades entered before they existed, run
`scripts\backfill_daily_progress.py` once (`--dry-run` to preview).

#### `failed_breakouts`
For tracking signal quality over time. Phase 2 will populate this.
```
(symbol, breakout_date) PRIMARY KEY
failure_date, pattern, original_score: ...
```

#### `run_log`
One row per job execution. Useful for debugging "why didn't morning_scan run yesterday?"
```
id PRIMARY KEY AUTOINCREMENT
job_name: TEXT  ('morning_scan' / 'preclose_scan' / 'eod_settle')
started_at, finished_at: TEXT (ISO timestamps)
status: TEXT  (RUNNING / SUCCESS / FAILED / PARTIAL)
alerts_generated: INTEGER
error_message: TEXT
```

---

## 15. Step-by-step: running the system

### First time setup (already done)

```powershell
# 1. Clone or unzip the project
cd "C:\Users\kutkar01\OneDrive - dentsu\Desktop\Personal\BreakoutStockAnalyser"

# 2. Create a venv and install dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install smartapi-python pyotp logzero websocket-client

# 3. Copy config templates and fill in credentials
copy config.example.yaml config.yaml      # (already done — edit capital/risk if needed)
copy .env.example .env                    # (already done — your SmartAPI creds are filled in)

# 4. Verify SmartAPI works
.\.venv\Scripts\python.exe test_smartapi.py
# Should print "[OK] SmartAPI integration verified."

# 5. Run the test suite
.\.venv\Scripts\python.exe -m pytest tests/
# Should print "78 passed"
```

### Daily workflow (currently manual; can be automated later)

Each trading day (Mon–Fri, excluding NSE holidays):

**At 9:30 AM IST** (after market open):

```powershell
.\.venv\Scripts\python.exe -m breakout.jobs.morning_scan
```

This refreshes prices, scans the universe, populates `setup_watchlist`. Look at the final log line `morning_scan complete: N symbols on setup watchlist`. Inspect with:

```powershell
.\.venv\Scripts\python.exe -c "
from breakout.config import load_config
from breakout.data.store import Store
cfg = load_config()
with Store(cfg.paths.workbook) as s:
    rows = s.read_setup_watchlist()
    print(f'{len(rows)} setups on watchlist:'); print('-'*80)
    print(f'{\"symbol\":<14}{\"pattern\":<22}{\"score\":>6}  {\"breakout\":>9}  notes')
    print('-'*80)
    for r in rows[:30]:
        print(f'{r[\"symbol\"]:<14}{r[\"pattern\"]:<22}{r[\"score\"]:>6.1f}  {r[\"breakout_level\"]:>9.2f}  {r[\"notes\"][:40]}')
"
```

This gives you the morning's candidates. Save the script as `inspect_watchlist.py` if you want to run it repeatedly.

**At 3:00 PM IST** (last 25 minutes):

```powershell
.\.venv\Scripts\python.exe -m breakout.jobs.preclose_scan
```

This confirms breakouts from the watchlist and emits alerts. Look at `output/alerts_YYYY-MM-DD.csv` for the day's alerts.

**At 4:00 PM IST** (after market close):

```powershell
.\.venv\Scripts\python.exe -m breakout.jobs.eod_settle
```

This walks every open paper trade through the state machine using today's settled OHLC. Inspect:

```powershell
.\.venv\Scripts\python.exe -c "
from breakout.config import load_config
from breakout.data.store import Store
from breakout.paper.tracker import OPEN_STATES, CLOSED_STATES
cfg = load_config()
with Store(cfg.paths.workbook) as s:
    open_t = s.read_paper_trades_by_state(*OPEN_STATES)
    closed_t = s.read_paper_trades_by_state(*CLOSED_STATES)
    print(f'Open: {len(open_t)}  Closed: {len(closed_t)}')
    for t in closed_t[-10:]:
        print(f\"  {t['symbol']:<10} {t['state']:<14} pnl_r={t['pnl_r'] or 0:+.2f}\")
"
```

### Ad-hoc validation

To force a fresh universe fetch (instead of waiting for the 7-day cache),
open `data_cache/breakout.xlsx` in Excel and delete the rows in the `universe`
sheet (leave the header), then save. The next scan sees an empty universe and
refetches. Alternatively, clear it programmatically:

```powershell
.\.venv\Scripts\python.exe -c "from breakout.config import load_config; from breakout.data.store import Store; cfg=load_config()
with Store(cfg.paths.workbook) as s: s.upsert_universe([]); s._sheets['universe'] = s._sheets['universe'].iloc[0:0]; print('Universe cleared.')"
```

To check a specific symbol's analysis manually:

```python
# save as inspect_symbol.py
import sys
from breakout.config import load_config
from breakout.data.fetcher import make_fetcher
from breakout.analysis.indicators import add_standard_indicators
from breakout.analysis.patterns import detect_all
from breakout.analysis.stage import classify_stage

sym = sys.argv[1].upper()
cfg = load_config()
df = make_fetcher(cfg).fetch_history(sym, days=300)
df = add_standard_indicators(df)
print(f'{sym}: stage = {classify_stage(df).value}')
for m in detect_all(df, cfg.patterns.enabled):
    print(f'  {m.pattern_name}: confidence={m.confidence:.0f} breakout={m.breakout_level:.2f}')
```

Run with `.\.venv\Scripts\python.exe inspect_symbol.py TITAN`.

---

## 16. Expected outputs

### Console output (morning_scan)

```
==================================================
morning_scan starting
universe cache is 0 days old, skipping refresh
universe: 500 symbols (refreshed=False)
fetching prices for 500 symbols via AngelOneFetcher
prices: fetched=478, failed=22, already-current=0
skip summary: {'insufficient_data': 3, 'quality': 47, 'stage': 354, 
                'no_pattern': 65, 'far_from_breakout': 8, 'low_score': 4}
morning_scan complete: 19 symbols on setup watchlist
```

The skip summary is gold. It tells you exactly where the funnel narrowed. In the above hypothetical:
- 3 symbols had insufficient history (probably recent listings).
- 47 failed the quality floor (too illiquid or too small).
- **354 weren't in Stage 2** — that's the big filter doing its job.
- 65 of the survivors had no detectable pattern (most stocks aren't setting up at any given time).
- 8 had patterns but weren't near breakout yet.
- 4 were near breakout but scored below 50.
- **19 made it through.**

### CSV output (preclose_scan)

`output/alerts_2026-05-20.csv`:

```csv
alert_type,symbol,score,pattern,breakout_level,entry_price,stop_loss,target_1,target_2,position_size,volume_ratio,stage,rs_rank,tightness,sector,notes
BREAKOUT,TITAN,68.5,nr7,4180.50,4182.00,4155.20,4235.60,4310.00,186,2.30,STAGE_2,0.0,0.0,flat,"adv=85.2cr near_pivot_high=True"
BREAKOUT,VARROC,72.1,darvas_box,720.00,722.50,704.80,758.00,790.00,282,1.85,STAGE_2,0.0,0.0,flat,"touches=5 ..."
```

Sorted by score descending. Each row is an actionable alert with everything you need to place the trade.

### Workbook sheets (after a day of running)

- `prices`: ~125,000 rows (500 symbols × 250 days)
- `universe`: 500 rows
- `setup_watchlist`: 10–40 rows (replaced each morning)
- `pullback_watchlist`: 0–20 rows (rolling 10 days)
- `paper_trades`: cumulative; grows over time
- `run_log`: 3 rows per trading day

### Logs

`logs/2026-05-20.log` — every job's INFO+DEBUG output. Useful when something didn't behave as expected. The same content as console (minus rich formatting).

---

## 17. Phase status

### Phase 1 (Done ✓)

Working MVP that you can run today.

- ✅ Project scaffolding (config, env, gitignore, package)
- ✅ Config loader with frozen-dataclass typed access
- ✅ Logging (file + colored console)
- ✅ Excel workbook store with full schema
- ✅ NIFTY 500 universe loader (NSE CSV with 7-day cache)
- ✅ Data fetcher (YFinanceFetcher + AngelOneFetcher + factory with fallback)
- ✅ Indicators: SMA, EMA, RSI, ATR, MACD, BB, ADX, volume ratio
- ✅ Pivot detector with strength scoring
- ✅ 3 pattern detectors: 52w_high, Darvas, NR7
- ✅ Weinstein stage classifier (1/2/3/4)
- ✅ Quality filter (with pledge stubbed)
- ✅ Composite scoring (with RS/tightness/sector defaulted to 0)
- ✅ Paper trade state machine with position sizing
- ✅ Pluggable alert dispatcher (CSV active)
- ✅ Three job entry points (morning, preclose, eod)
- ✅ 78 tests
- ✅ End-to-end smoke test

### Phase 2 (Next)

The confirmation layer. Significantly improves scoring quality.

- ⏳ Six more patterns: inside_bar, bollinger_squeeze, ascending_triangle, vcp, cup_and_handle, flag
- ⏳ Tightness scoring (`analysis/tightness.py`): ATR(10)/ATR(50) contraction
- ⏳ Relative strength scoring (`analysis/rs.py`): 63-day return percentile vs NIFTY 500
- ⏳ Sector context (`analysis/sectors.py`): which sector each stock belongs to + sector trend
- ⏳ Pullback watchlist confirmation logic in `preclose_scan` — alert when a recent breakout retests its level
- ⏳ Earnings blackout filter (need to source corporate-actions data)
- ⏳ Market mood gate (India VIX vs 90-day SMA)
- ⏳ Promoter pledge data source (Screener.in scrape or nsepython)
- ⏳ Email dispatch
- ⏳ Teams/Slack integration (planned to replace Telegram)
- ⏳ Daily digest output (paper-trade performance summary)

After Phase 2, alert scores will span the full 0–100 range and high-scoring setups will be meaningfully more distinguishable from borderline ones.

### Phase 3 (Later)

Validation and polish.

- ⏳ Backtest harness (replay any past N days against current logic, produce summary stats)
- ⏳ `paper/stats.py` — win rate by pattern / score band / sector
- ⏳ Failed-breakout detection (stocks that closed back below breakout within 3 days)
- ⏳ Exit-signal alerts for paper trades stopped within 3 days
- ⏳ Windows Task Scheduler XML for autonomous scheduling
- ⏳ Polish: retry logic, more robust error handling, edge cases

---

## 18. Configuration reference

`config.yaml` — every setting explained.

```yaml
universe: "nifty500"
# Which universe to scan. Currently only "nifty500" is supported.

data_source: "angelone"
# "angelone" (production) or "yfinance" (dev fallback).
# If "angelone" but .env creds are missing, automatically falls back to yfinance.

risk:
  capital: 200000
  # Your total trading capital in INR. Used for position sizing only;
  # the scanner doesn't track actual P&L against this.

  risk_per_trade_pct: 2.5
  # % of capital risked per trade. If stop hits, you lose this %.
  # Conservative: 1.0  Moderate: 2.0  Aggressive: 3.0+
  # Mathematical max position size = capital × risk_pct / (entry - stop).

  max_concurrent_positions: 8
  # Enforced at entry. With 8 positions at 2.5% each, total at-risk = 20%.

  max_positions_per_sector: 3
  # Open positions allowed to share a sector. Without this, 8 positions can be
  # one bet — breakouts cluster by sector. 0 disables either limit. See § 12.

scoring_weights:
  # These are not currently read by scoring.py — the weights are hardcoded
  # in `composite_score`. Reading them dynamically is a Phase 2 polish item.
  pattern_quality: 35
  stage: 20
  relative_strength: 15
  volume: 15
  tightness: 10
  sector: 5

thresholds:
  min_score_to_alert: 60
  # Alerts emitted only if composite score >= this. Watchlist is 50 (hardcoded).

  min_volume_ratio: 1.5
  # Breakout candle volume / 20-day avg. Below this = no confirmation. Also the
  # knee of the volume score: this ratio earns half the 15-point weight.

  volume_saturation_ratio: 3.0
  # Where the volume score reaches full weight. Raise it to demand a bigger
  # spike for full credit; lower it back toward min_volume_ratio to restore the
  # old saturate-at-the-gate behaviour (not recommended — see § 11).

  extension_free_pct: 2.0
  extension_max_pct: 8.0
  extension_max_penalty: 10.0
  # Chase penalty. No penalty up to `free`, ramping to `max_penalty` points off
  # at `max_pct` above the breakout level, capped beyond. Set max_penalty: 0 to
  # disable while still logging `extension_pct` per alert.

  min_market_cap_cr: 500
  # Quality floor. INR crores.

  min_adv_cr: 5
  # Quality floor: average daily turnover, INR crores.

  min_listing_days: 365
  # Reject stocks listed less than this. Avoids IPO weirdness.

  near_breakout_pct: 2.0
  # Setup watchlist: only include stocks within X% of their breakout level.

  pullback_window_days: 10
  # Pullback watchlist: rolling window. Older entries get pruned.

  pullback_retest_band_pct: 3.0
  # Pullback re-entry: today's low must come within X% above breakout.

pivots:
  swing_n: 5
  # Bars each side for a confirmed pivot. 5 is default; 3 = faster; 10 = stricter.

patterns:
  enabled:
    - fifty_two_week_high
    - darvas_box
    - nr7
  # List of patterns to run. Phase 2 will add 6 more.

stage_filter:
  sma_weeks: 30           # 30 weeks × 5 trading days = 150-day SMA.
  slope_lookback_weeks: 10  # Slope measured over the last 10 weeks (50 bars).
  high_lookback_weeks: 10   # Higher-high check window.

output:
  csv: true
  # CSV file at output/alerts_YYYY-MM-DD.csv. Always recommended.

  email: false
  # Not implemented in Phase 1.

paper_trading:
  enabled: true
  # Set to false to skip writing to the paper_trades table.

  hold_max_days: 30
  # Time-exit threshold. Trades held this long without resolution close at close.

  # `alert_ttl_days` was removed on 2026-09-12. It governed ALERTED -> CANCELED
  # under the old next-day-open entry model; trades now open as ENTERED at the
  # 3 PM confirmation, so there is no waiting period for it to expire. A
  # leftover key in your config.yaml is ignored with a warning, not a crash.

  atr_stop_multiplier: 1.5
  # stop = breakout_level - (multiplier × ATR(14)).

  target_1_r_multiple: 2.0
  # target_1 = entry + (multiple × (entry - stop)).

regime:
  enabled: true
  # Scale position size by market regime (§ 10). false keeps measuring and
  # logging the regime but pins the multiplier at 1.0.

  nifty_symbol: "^NSEI"
  nifty_sma: 200
  nifty_slope_lookback: 20
  # The index-trend vote: close vs a rising/falling 200DMA.

  breadth_sma: 50
  breadth_up_pct: 60    # >= this % of the universe above its 50DMA is a +1 vote
  breadth_down_pct: 40  # <= this % is a -1 vote

  risk_off_multiplier: 0.5   # regime score <= -2
  severe_multiplier: 0.25    # regime score == -3 (all three votes negative)
  # Priors, not findings. `regime` / `regime_score` / `breadth_pct` are logged
  # per alert so these can be re-set from realised outcomes.

costs:
  enabled: true
  # Net round-trip transaction costs off every settled trade (paper and
  # backtest). Set false to see the old gross-only numbers. See § 12.

  brokerage_pct: 0.1
  brokerage_max_inr: 20
  # Broker-specific: Angel One charges the lower of the two per executed order.

  stt_pct: 0.1              # both legs
  exchange_txn_pct: 0.00297 # NSE
  sebi_turnover_pct: 0.0001
  stamp_duty_pct: 0.015     # buy leg only
  gst_pct: 18               # on brokerage + exchange + SEBI
  # Statutory; change only if the SEBI/NSE schedule changes.

  slippage_pct: 0.05
  # Not statutory — a haircut on the idealised fill, per leg. Raise it if the
  # names you trade are thin.

paths:
  data_cache: "data_cache"   # Excel workbook + cached OHLCV
  logs: "logs"               # Per-day log files
  output: "output"           # CSV alert files
  workbook: "data_cache/breakout.xlsx"   # single workbook, one sheet per table

logging:
  level: "INFO"     # DEBUG / INFO / WARNING / ERROR
  console: true     # Also log to console (rich-formatted)
```

`.env` — credentials only. Never committed to git.

```bash
# Angel One SmartAPI (production data source)
ANGELONE_API_KEY=<from smartapi.angelbroking.com>
ANGELONE_CLIENT_CODE=<your Angel One login ID, e.g. K236239>
ANGELONE_PIN=<your Angel One PIN, 4 digits>
ANGELONE_TOTP_SECRET=<base32 string from Angel One -> Enable TOTP>

# Email SMTP (Phase 2)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=<your email>
SMTP_PASSWORD=<Gmail app password>
SMTP_TO=<recipient email>
```

---

## 19. Troubleshooting

### "ModuleNotFoundError: No module named 'X'"

You're running with system Python instead of the venv. Use:
```powershell
.\.venv\Scripts\python.exe <script>
```
Or activate the venv first:
```powershell
.\.venv\Scripts\Activate.ps1
```
If activation errors with "running scripts is disabled":
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "UnicodeEncodeError" in console output

Windows' default `cp1252` encoding can't print ₹, →, ✓, etc. Either:
1. Avoid those characters in print statements (use Rs, ->, [OK]).
2. Force UTF-8 stdout at script start:
   ```python
   import sys
   if hasattr(sys.stdout, "reconfigure"):
       sys.stdout.reconfigure(encoding="utf-8")
   ```

### Angel One login fails

- Check `.env` has all four fields filled in.
- Check the TOTP secret is the *base32 string*, not the 6-digit code shown in your authenticator app.
- TOTP requires accurate system clock — if your Windows clock is off by more than 30 seconds, TOTP won't match.

### `smartapi-python` ImportError on logzero / websocket-client

The package doesn't declare these as deps. Install them:
```powershell
.\.venv\Scripts\python.exe -m pip install logzero websocket-client
```

### NSE universe download fails with 403

NSE sometimes blocks anonymous requests. Wait 5 minutes and retry. If persistent, drop a `nifty500_fallback.csv` in the project root and ask me to wire it in.

### yfinance returns empty for some symbols

Known yfinance quirk with NSE symbols — HDFCBANK, MARUTI, ICICIBANK sometimes return empty. Switch to Angel One for reliability.

### Tests fail

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```
Read the failure output. If it's an indicator or pattern test, the fixture or threshold may need adjustment. If it's the end-to-end test, check that config.yaml exists and is valid YAML.

### morning_scan reports 0 symbols on watchlist

Three common causes:
1. **Current market is in correction** — most stocks fail Stage 2. Check `skip summary` — if `stage` is the dominant skip reason, that's the market, not a bug.
2. **Universe is the seeded 5-symbol mini-universe** — clear with the one-liner in §15.
3. **Thresholds too strict** — try lowering `near_breakout_pct` to 3.0 or `min_volume_ratio` to 1.3 in `config.yaml`.

### "no Angel One NSE token for symbol X"

The instrument master only contains `-EQ` suffixed NSE equities. Some symbols (T2T series, BE series, indices) won't be there. Symbols in the NIFTY 500 should all resolve; if not, refresh the instrument master cache by restarting morning_scan.

---

## 20. Glossary

| Term | Definition |
|---|---|
| **ADV** | Average Daily Turnover. Average of close × volume over a window (default 20 days). Liquidity proxy. |
| **ATR** | Average True Range. Average size of the daily price range over a window, accounting for gaps. Volatility measure. |
| **Base** | The consolidation period before a breakout. The "base height" is the price range of this consolidation. |
| **Base height** | High of base − low of base. Used to project the measured-move target. |
| **Bollinger Bands** | 20-day SMA ± 2 standard deviations. Envelope showing typical price range. |
| **Breakout level** | The price at which the stock confirms a breakout. Often the resistance line of the pattern. |
| **Breakout candle** | The bar that closes above the breakout level on volume — the confirmation bar. |
| **CAN SLIM** | William O'Neil's investment methodology. Includes Cup & Handle pattern. |
| **Crore** | 10^7 (10 million) in Indian numbering. ₹500 cr = ₹50 crore = ₹5 billion. |
| **Darvas Box** | A flat-topped, flat-bottomed consolidation. Pattern named after Nicolas Darvas. |
| **EOD** | End of Day. After market close. |
| **F&O** | Futures and Options. Indian derivatives segment. |
| **FII** | Foreign Institutional Investors. Their flows are a big driver of Indian markets. |
| **IST** | Indian Standard Time (UTC+5:30). |
| **MACD** | Moving Average Convergence Divergence. Trend/momentum oscillator. |
| **Max favorable excursion** | The highest unrealized profit during a trade. Tells you "how much was on the table?" |
| **Max adverse excursion** | The worst unrealized loss during a trade. Tells you how much heat you took. |
| **Measured move** | Price target projected by adding the base height to the breakout level. |
| **NR7** | Narrow Range 7. A bar whose true range is the smallest of the last 7 bars. |
| **NSE / BSE** | National / Bombay Stock Exchange. NSE is the larger; we use NSE tickers throughout. |
| **OHLC / OHLCV** | Open, High, Low, Close (+ Volume). The standard daily bar. |
| **Pivot** | A confirmed swing high or swing low. The structural skeleton of price action. |
| **Profit factor** | Gross winners / gross losers. > 1.5 = good edge. < 1.0 = losing. |
| **R / R-multiple** | Risk-multiple. 1R = the risk per share = entry − stop. A +2R trade made twice your risk. |
| **RBI** | Reserve Bank of India. Their policy moves affect rate-sensitive sectors. |
| **RS / Relative Strength** | A stock's return divided by an index's return over the same period. RS > 1 = outperforming. |
| **RSI** | Relative Strength Index. 0–100 oscillator. >70 overbought, <30 oversold. |
| **SEBI** | Securities and Exchange Board of India. The market regulator. |
| **SMA** | Simple Moving Average. |
| **SmartAPI** | Angel One's REST API for market data and order placement. |
| **Stage 1/2/3/4** | Weinstein's classification of a stock's life cycle. We only trade Stage 2. |
| **Stop loss** | The price at which you exit a losing position. Set below the breakout level. |
| **Swing trade** | A trade held a few days to a few weeks. Our target horizon. |
| **T+1 settlement** | Indian equities settle one trading day after the trade. Standard since 2023. |
| **Target 1** | First profit target. Typically 2:1 reward-to-risk. |
| **Target 2** | Second profit target. Measured-move projection. |
| **Time exit** | Forced exit after holding a position for a maximum number of days. |
| **TOTP** | Time-based One-Time Password. The 6-digit code your authenticator app generates. |
| **True range** | Max of (high − low), abs(high − prev_close), abs(low − prev_close). Accounts for gaps. |
| **VCP** | Volatility Contraction Pattern. Minervini's sequence of progressively shallower pullbacks. |
| **Watchlist** | The list of candidates the scanner is tracking. Setup watchlist = pre-breakout; pullback watchlist = post-breakout retests. |
| **Weinstein** | Stan Weinstein. Author of "Secrets for Profiting in Bull and Bear Markets." Defined the four-stage model. |
| **Wilder's smoothing** | Specific EMA variant used in ATR, RSI, ADX. Equivalent to `EMA(alpha = 1/period)`. |

---

End of guide. If something here is unclear or you spot an error, flag it and I'll update.
