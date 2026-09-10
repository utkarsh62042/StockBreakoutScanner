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
- **Alerts (CSV file, optionally Telegram)** — issued in the last 25 minutes of the trading session for stocks that *actually confirmed* a breakout that day (closed above level with above-average volume). Each alert includes precise entry, stop-loss, two targets, and position size sized to your risk-per-trade setting.
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
| **`requests`** | For the NSE CSV download and (Phase 2) Telegram dispatch. |
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
│ Watchlist (workbook) → Alerts (CSV / Telegram)              │
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
| `breakout/data/store.py` | `Store(workbook_path)` — every read/write to the Excel workbook goes through here. Use as a context manager (flushes on exit). |
| `breakout/data/universe.py` | `refresh_universe_if_stale(store)` — downloads NIFTY 500 CSV from NSE if cache is >7 days old. |
| `breakout/data/fetcher.py` | `make_fetcher(cfg)` returns YFinance or AngelOne based on config + credential availability. Both implement `fetch_history(symbol, days)` and `fetch_history_batch(symbols, days)`. Prefer the batch call in loops — YFinance overrides it to fetch ~50 tickers per HTTP request (8x faster over a full universe). **Angel One is disabled as of 2026-09** — its `getCandleData` returns AB1021 "Too many requests" even at 8s spacing (server-side bug on their end, see the fetcher docstring). |
| `breakout/analysis/pivots.py` | `find_pivots(df, n=5)` — confirmed swing highs and swing lows. Every pattern depends on this being right. |
| `breakout/analysis/indicators.py` | Pure functions: `sma`, `ema`, `rsi`, `atr`, `macd`, `bollinger_bands`, `adx`, `volume_ratio`. Plus `add_standard_indicators(df)` attaches all of them as columns. |
| `breakout/analysis/patterns.py` | `detect_fifty_two_week_high_breakout`, `detect_darvas_box`, `detect_nr7`. Plus `detect_all(df, enabled)` runs the enabled set and returns matches sorted by confidence. |
| `breakout/analysis/stage.py` | `classify_stage(df)` returns `Stage.STAGE_1` through `STAGE_4` or `UNKNOWN`. |
| `breakout/filters/quality.py` | `check_quality(df, metadata, thresholds)` — hard gate; returns `QualityResult(passed, reasons_failed)`. |
| `breakout/paper/tracker.py` | `position_size`, `compute_stop`, `compute_targets`, `insert_alert`, `settle_one_trade`, `apply_outcome`. The full paper-trade lifecycle. |
| `breakout/output/alerts.py` | `Alert` dataclass, `CSVChannel`, `TelegramChannel` (stubbed), `build_channels(cfg)`, `dispatch_alerts(alerts, channels)`. |
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

### Quality floor (`breakout/filters/quality.py`)

Hard gate: a stock either passes or fails. Reasons for failure:
- **`adv_below_floor`** — average daily turnover < ₹5 cr over last 20 days. Illiquid.
- **`market_cap_below_floor`** — market cap < ₹500 cr. Penny / microcap territory.
- **`too_recent_listing`** — listed < 365 days. Not enough chart history.
- **`high_promoter_pledge`** — promoter pledge ≥ 30%. (Phase 2 — currently default-pass because there's no free clean data source for this yet.)

ADV is computed directly from the price/volume data, no metadata needed. The other three depend on the universe table having those fields populated (NSE's CSV doesn't include market cap, so it's None for now — that check default-passes too in Phase 1).

### Earnings blackout (Phase 2)

Exclude stocks within ±5 trading days of an earnings announcement. Patterns get disrupted by news events; breakouts on earnings day are not the breakouts this strategy is built for. Default-pass in Phase 1 (no source wired yet).

### Market mood (Phase 2)

When India VIX is above its 90-day SMA, breakout patterns historically fail more often. Phase 2 will optionally gate alerts on this. Currently no gate.

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
        + 20                                          # Stage 2 (gated above)
        + min(15, features.rs_score)                  # max 15
        + min(15, features.volume_ratio / 1.5 * 15)   # max 15
        + min(10, features.tightness_score * 10)      # max 10
        + {"up": 5, "flat": 3, "down": 0}.get(features.sector_trend, 3)  # max 5
    )
```

### Why these weights

- **Pattern quality 35%** — biggest contributor. A confident pattern (e.g., a clean Darvas with 6 touches) is much more reliable than a barely-detected one.
- **Stage 20%** — second-biggest. Already a hard gate; the points reward you for being in the right environment.
- **Relative strength 15%** — a stock outperforming the market is in demand. Often the best Stage-2 advances come from RS leaders.
- **Volume 15%** — confirms institutional participation. Without it, even a clean pattern is "retail interest" only.
- **Tightness 10%** — pre-breakout compression. Important but secondary; many patterns work without it.
- **Sector 5%** — small modifier. A rising sector helps but isn't decisive.

### Thresholds

| Threshold | Where it's enforced | Default |
|---|---|---|
| Watchlist threshold | `morning_scan` — anything ≥ this goes on `setup_watchlist` | 50 |
| Alert threshold | `preclose_scan` — must score ≥ this *and* confirm breakout to alert | 60 (`min_score_to_alert` in config) |

The 10-point buffer between watchlist (50) and alert (60) accounts for the volume boost that comes on actual breakout day. A stock can sit at 55 on the watchlist (moderate volume), then jump to 75 if today's volume spikes to 3× average.

In Phase 1, with RS / tightness / sector stubbed to 0, the practical max score is:
- Pattern 35 + Stage 20 + Volume 15 + Sector (flat) 3 = **73**

So Phase 1 alerts cluster between 50 and 73. Phase 2's additional signals open up the 73–100 range for genuinely standout setups.

---

## 12. Paper trading

`breakout/paper/tracker.py`. Every alert becomes a virtual position. After 4–6 weeks of running, the `paper_trades` table answers: *what's my actual edge?*

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
ALERTED
   │
   ├── (next trading day at open) ──→ ENTERED
   │
   └── (>= alert_ttl_days with no confirmation) ──→ CANCELED

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

### Position sizing

`position_size(capital, risk_pct, entry, stop)`:

```
risk_amount = capital × (risk_pct / 100)
risk_per_share = entry - stop
shares = floor(risk_amount / risk_per_share)
```

For your config: ₹2,00,000 × 2.5% = ₹5,000 risk per trade. If a stock has entry ₹500 and stop ₹485 (risk ₹15/share), you'd take 5000 / 15 ≈ 333 shares.

This is **risk-based sizing**, not equal-rupee sizing. Every position risks the same amount if the stop hits — wider stops = fewer shares, tighter stops = more shares. The key property is that your maximum loss per trade is consistent regardless of the stock's price or volatility.

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

### Time exit

If neither stop nor target hits within `hold_max_days` (default 30 trading days), close at that day's close. The intuition: a breakout that hasn't worked in 6 weeks is probably not going to. Better to free up the capital.

### Tracked metrics per closed trade

- Entry date, entry price
- Exit date, exit price
- PnL in INR, PnL in R-multiples
- Days held
- Max favorable excursion (highest unrealized profit during trade)
- Max adverse excursion (worst unrealized loss)

The max-favorable / max-adverse fields let you analyze "did I leave money on the table?" — if max favorable was +4R but you exited at +2R, the trade had more in it.

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
   - Emit an `Alert` through every enabled output channel (CSV, optionally Telegram).
   - Insert a paper trade row with state = `ALERTED`.
   - Add the stock to the `pullback_watchlist` so future retests can be flagged.

**Output:**
- A `rich` table to console showing each confirmed alert.
- `output/alerts_YYYY-MM-DD.csv` with full alert details (or appends if file exists from morning).
- Optionally, a Telegram message per alert (Phase 1: stub; Phase 2: live).
- Run log entry in the workbook.

**Why the 3:00 PM timing:** Closing breakouts have a meaningfully higher success rate than intraday breakouts. Stocks that broke out at 11 AM might sell off by 2 PM (false breakout). Waiting until the final 25 minutes filters those out — institutional commitment shows in the close.

### EOD settle — `jobs/eod_settle.py`

**Scheduled time:** 4:00 PM IST (after market close).

**What it does:**
1. Read every paper trade in an OPEN state (`ALERTED`, `ENTERED`, or `TARGET_1_HIT`).
2. For each, fetch today's settled OHLC.
3. Walk through the state machine:
   - `ALERTED` from yesterday → transition to `ENTERED` using today's open (entry price = today's open).
   - `ALERTED` for > `alert_ttl_days` (default 2) → close as `CANCELED`.
   - `ENTERED` with today's low ≤ stop → close as `STOPPED_OUT`.
   - `ENTERED` with today's high ≥ target_2 → close as `TARGET_HIT`.
   - `ENTERED` with today's high ≥ target_1 (but not target_2) → transition to `TARGET_1_HIT` (partial; stop moves to entry).
   - `ENTERED` with days_held ≥ `hold_max_days` (default 30) → close as `TIME_EXIT` at today's close.

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
notes: TEXT  (compact details: 'adv=12.5cr dist=0.85% touches=4 ...')
```

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
pnl_inr, pnl_r: REAL  (R = R-multiples)
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
.\.venv\Scripts\python.exe -m pip install -e .[telegram]
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
- ✅ Pluggable alert dispatcher (CSV active, Telegram stubbed)
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
- ⏳ Telegram dispatch (currently stubbed)
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
  # Not enforced yet; advisory. With 8 positions at 2.5% each, total at-risk = 20%.

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
  # Breakout candle volume / 20-day avg. Below this = no confirmation.

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

  telegram: false
  # Set to true and fill TELEGRAM_* in .env to enable Telegram dispatch.
  # Phase 2 will wire this; for Phase 1, set it to true with credentials and
  # the channel will be built but the implementation is minimal.

  email: false
  # Not implemented in Phase 1.

paper_trading:
  enabled: true
  # Set to false to skip writing to the paper_trades table.

  hold_max_days: 30
  # Time-exit threshold. Trades held this long without resolution close at close.

  alert_ttl_days: 2
  # ALERTED -> CANCELED if no confirmation within this many days.

  atr_stop_multiplier: 1.5
  # stop = breakout_level - (multiplier × ATR(14)).

  target_1_r_multiple: 2.0
  # target_1 = entry + (multiple × (entry - stop)).

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

# Telegram (Phase 2)
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=<your numeric chat id>

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
