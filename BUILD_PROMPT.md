# Build Prompt: Indian Stock Breakout Scanner

> **How to use this file:** Copy the entire contents of this file (everything below the horizontal rule) into Claude in your VS Code. It contains the complete specification for building the system. Claude will first ask you a small set of setup questions, then build the project in phases, testing each phase before moving on.

---

## Role and Context

You are building a local Python application called **`breakout_scanner`** for the **Indian stock market** (NSE/BSE). The user is a swing trader based in India who holds positions for a few days to a few weeks. They want a tool that runs twice daily on their personal laptop, scans the NIFTY 500 universe, identifies high-probability breakout setups, and tracks results via an integrated paper trading system.

This is a single-user, local-only system. **No web server, no cloud, no real-time polling.** Two scheduled batch jobs per trading day, that's it.

The user has reviewed and approved the overall design documented in `README.md`. Your job is to implement it correctly, in phases, testing as you go.

## CRITICAL: Read Before Coding

1. **Read `README.md` in this directory first.** It contains the strategy, architecture, file structure, and user-facing documentation. The README is the contract; this prompt is the implementation guide.

2. **Ask the user the setup questions in the next section before writing any code.** Several decisions (data source, output method, OS, risk settings) depend on the user's environment and preferences.

3. **The market is the Indian market.** Every time component is IST. Every currency is INR. NSE tickers use the `.NS` suffix in yfinance; BSE uses `.BO`. Use `RELIANCE.NS`, `TCS.NS`, etc.

4. **The pivot detection in `breakout/analysis/pivots.py` is the most important file in the project.** Every chart pattern depends on it. Write thorough tests for it first, with hand-checked fixtures. If pivots are wrong, every pattern detector downstream produces garbage.

## Setup Questions (Ask the User Before Building)

Ask these in a single message, formatted as a numbered list. Wait for answers before proceeding.

1. **Operating system?** (Windows / macOS / Linux — determines scheduler: Task Scheduler vs cron)
2. **Data source?** Three options:
   - `yfinance` (no account needed — recommended if you don't already have a broker API key)
   - `angelone` (Angel One SmartAPI — needs an Angel One demat account and API credentials)
   - `upstox` (Upstox API — needs an Upstox demat account and API credentials)
3. **Output method?** `csv_only` / `csv_plus_telegram` / `csv_plus_email` (Telegram and email can be added later — `csv_only` is the simplest start.)
4. **Trading capital (INR)?** Used to calculate position sizes. Default: `500000`.
5. **Risk per trade (%)?** Default: `1.0` (means risk 1% of capital per trade).
6. **Should I set up the cron / Task Scheduler entries automatically?** `yes` / `no` (if no, you'll get a manual setup guide.)
7. **Initialize a Git repo?** `yes` / `no`.

After collecting answers, write them to `config.yaml` and proceed to Phase 1.

## Technology Stack

- **Python 3.10+**
- **Dependency manager:** `uv` (preferred — faster, simpler than poetry)
- **Core libraries:**
  - `pandas`, `numpy` — data manipulation
  - `yfinance` + `yfinance-cache` — price data (if user picks yfinance)
  - `smartapi-python` — Angel One SmartAPI (if user picks angelone)
  - `upstox-python-sdk` — Upstox (if user picks upstox)
  - `scipy` — for pattern detection math (linear regression on swing points)
  - `pyyaml` — config loading
  - `sqlalchemy` or raw `sqlite3` — SQLite persistence (prefer raw sqlite3 for simplicity here)
  - `pytest` — testing
  - `rich` — pretty console output
  - `python-telegram-bot` (only if Telegram chosen)
  - `apscheduler` (optional, for in-process scheduling if cron not used)
- **No deep learning libraries.** No `tensorflow`, no `torch`. This is rule-based, not ML.

## Strategy Specification (Authoritative)

### The two daily jobs

**Morning scan (`morning_scan.py`)** — runs at 9:30 AM IST.

1. Refresh NIFTY 500 universe if older than 7 days.
2. Update price data for all 500 tickers (last 250 trading days of daily OHLCV).
3. For each ticker, run:
   - Quality filter (hard pass/fail)
   - Stage classification (only Stage 2 advances)
   - Pattern detection (run all 9 detectors)
   - Indicators (RSI, MACD, ATR, ADX, BB width, volume MA)
   - Tightness measurement
   - Relative strength vs NIFTY 500
   - Sector context lookup
4. For each surviving ticker, compute composite score.
5. Populate two watchlists:
   - **Setup watchlist:** Score ≥ 50, detected pattern, within 2% of breakout level. (Stocks "about to" break out.)
   - **Pullback watchlist:** Carry forward yesterday's confirmed breakouts. Add stocks that broke out within the last 10 days and are now retesting their breakout level (within 3% above it).
6. Save to SQLite. Log run summary.

**Pre-close scan (`preclose_scan.py`)** — runs at 3:00 PM IST.

1. Re-fetch *intraday* (latest available) data for stocks in both watchlists only (not the full 500).
2. For **setup watchlist** stocks: check if current price > breakout level AND today's volume ≥ 1.5× 20-day avg volume. If yes, generate an `ALERT: BREAKOUT` for that ticker.
3. For **pullback watchlist** stocks: check if today's low touched the breakout level (within 1% above) AND current price is now above the breakout level AND today's candle shows a reversal (lower wick longer than body, OR close > open). If yes, generate `ALERT: PULLBACK_ENTRY`.
4. For each alert, calculate stop, targets, position size.
5. Write alerts to CSV. Dispatch via Telegram/email if configured.
6. Log new paper trade entries (state = `ALERTED`).

**EOD settle (`eod_settle.py`)** — runs at 4:00 PM IST.

1. For all open paper trades, fetch today's OHLC and check:
   - If today's low ≤ stop_loss → close as `STOPPED_OUT`.
   - Else if today's high ≥ target_2 → close as `TARGET_HIT` (target 2).
   - Else if today's high ≥ target_1 → mark `TARGET_1_HIT` (partial, still open with stop moved to entry).
   - Else if days_held ≥ 30 → close as `TIME_EXIT`.
2. For paper trades still in `ALERTED` state from yesterday: transition to `ENTERED` using today's open price. (Simulates the realistic case where the user acts on the alert at next-day open.)
3. For paper trades in `ALERTED` state from >2 days ago that never confirmed: close as `CANCELED`.
4. Update SQLite. Generate today's outcome line in the paper trade log.

### Pivot detection (the foundation)

A **swing high** at index `i` is a candle where `high[i]` is strictly greater than `high[i-k]` and `high[i+k]` for all `k` in `1..N`. Default N=5 for swing trading.

A **swing low** at index `i` is a candle where `low[i]` is strictly less than `low[i-k]` and `low[i+k]` for all `k` in `1..N`.

Implementation:

```python
def find_pivots(df: pd.DataFrame, n: int = 5) -> tuple[list[int], list[int]]:
    """
    Returns (swing_high_indices, swing_low_indices).
    A pivot at index i requires n candles on each side, so the most recent
    n candles cannot be confirmed as pivots yet.
    """
    highs, lows = [], []
    for i in range(n, len(df) - n):
        window_highs = df['high'].iloc[i-n:i+n+1].values
        window_lows = df['low'].iloc[i-n:i+n+1].values
        if df['high'].iloc[i] == window_highs.max() and (window_highs == df['high'].iloc[i]).sum() == 1:
            highs.append(i)
        if df['low'].iloc[i] == window_lows.min() and (window_lows == df['low'].iloc[i]).sum() == 1:
            lows.append(i)
    return highs, lows
```

Each pivot also gets a **strength** score = how many bars on each side it dominates (look further than N to find the answer).

**Test this exhaustively.** Use hand-crafted fixtures with known pivots. Edge cases to test: flat tops (multiple bars at same high), gaps, the first/last N candles (cannot be pivots).

### Pattern detectors (9 total)

Each detector lives in `analysis/patterns.py` and returns a dataclass:

```python
@dataclass
class PatternMatch:
    detected: bool
    pattern_name: str
    confidence: float  # 0-100
    breakout_level: float
    base_start_idx: int
    base_end_idx: int
    base_height: float  # for measured-move target
    notes: dict  # pattern-specific metadata
```

Implement in this order (easiest/most reliable first):

**1. `detect_52w_high_breakout()`** — Close is within 2% of the 52-week high (252 trading days). The breakout level is the 52-week high itself. Confidence scales with how flat the resistance is (i.e., how many times the stock has approached this level in the last 6 months without breaking).

**2. `detect_darvas_box()`** — Find a 20+ day window where the high stays within ±3% of a flat top (the "ceiling") and the low stays within ±5% of a flat bottom. Box must have at least 3 touches of the ceiling. Breakout level = ceiling.

**3. `detect_nr7()`** — Today's true range is the smallest of the last 7 days. Breakout level = today's high. Highest confidence when NR7 happens at or near a swing high.

**4. `detect_inside_bar_breakout()`** — Today's high < yesterday's high AND today's low > yesterday's low. Breakout level = yesterday's high (the "mother bar" high). Bonus confidence if it's also an NR7.

**5. `detect_bollinger_squeeze()`** — Bollinger Band width (using 20-period, 2 std dev) is at its 6-month minimum AND price is in the upper half of the bands. Breakout level = upper band.

**6. `detect_ascending_triangle()`** — Find the last 4+ swing highs in the lookback. Fit a horizontal line through them (max deviation 2%). Find the last 3+ swing lows. Fit a linear regression — slope must be positive and r² > 0.7. Pattern duration: 20–80 trading days. Breakout level = the flat resistance.

**7. `detect_vcp()`** — Volatility Contraction Pattern (Mark Minervini). Identify a sequence of 3+ pullbacks where each pullback's depth is at most 60% of the previous pullback's depth (e.g., -25%, -14%, -8%). Each pullback also shows decreasing volume vs the prior rally. Breakout level = highest high of the most recent rally.

**8. `detect_cup_and_handle()`** — Find a U-shape: (a) initial swing high (the left cup lip), (b) drawdown of 12–35% over 4–26 weeks, (c) recovery to within 5% of the left lip, forming the right lip. Then a "handle" pullback: shallow consolidation (4–20% deep, 1–5 weeks long) below the right lip but above the cup midpoint. Breakout level = right cup lip.

**9. `detect_flag()`** — A "pole" = a directional move of at least 15% in under 20 trading days with above-average volume. Followed by a "flag" = 5–15 days of consolidation in a tight range counter-trending slightly (i.e., flag trends down 3–10% if pole was up). Breakout level = top of flag channel.

**For each detector, write tests with synthetic OHLCV fixtures** that should detect, plus negative fixtures that should NOT detect. Aim for at least 3 positive + 3 negative fixtures per pattern.

### Stage classification (Stan Weinstein)

In `analysis/stage.py`:

- **Stage 1 (Accumulation):** Price oscillating around a flat 30-week SMA. SMA slope ~0.
- **Stage 2 (Advance):** Price > 30-week SMA, AND 30-week SMA slope > 0 over the last 10 weeks, AND most recent 10-week high > previous 10-week high.
- **Stage 3 (Distribution):** Price oscillating around a flattening 30-week SMA after a Stage 2.
- **Stage 4 (Decline):** Price < 30-week SMA, AND SMA slope < 0.

Only Stage 2 stocks pass through to scoring. Everything else is filtered out.

### Tightness / Volatility Contraction

In `analysis/tightness.py`:

Measure `tightness_score` = `1 - (ATR(10) / ATR(50))`. Values > 0.3 indicate strong contraction (tight). Values near 0 are normal. Negative values are expansion.

Tighter base before breakout → higher chance of successful breakout. Score component: `tightness_score × 10` capped at 10 points.

### Relative strength

In `analysis/rs.py`:

`RS = (stock_return_63d / nifty500_return_63d)`. Rank the 500 stocks by this value. Convert rank to 0–100 percentile. Stocks in top 25% (rank ≥ 75) get full 15 points. Linear scaling below that down to 0 at rank 50.

### Scoring formula

In `scoring.py`:

```python
def composite_score(features: dict) -> float:
    # Hard filters first — return 0 if any fail
    if not features['quality_pass']:
        return 0
    if features['stage'] != 'STAGE_2':
        return 0
    if features['earnings_blackout']:
        return 0
    if not features['pattern_match']:
        return 0

    pattern_pts   = features['pattern_confidence'] * 0.35       # max 35
    stage_pts     = 20 if features['stage'] == 'STAGE_2' else 0  # max 20
    rs_pts        = features['rs_score']                        # max 15
    volume_pts    = min(15, features['volume_ratio'] / 1.5 * 15) # max 15
    tightness_pts = min(10, features['tightness_score'] * 10)   # max 10
    sector_pts    = 5 if features['sector_trend'] == 'up' else (3 if features['sector_trend'] == 'flat' else 0)

    return pattern_pts + stage_pts + rs_pts + volume_pts + tightness_pts + sector_pts
```

Only alerts with `score ≥ min_score_to_alert` (default 60) are emitted.

### Position sizing

```python
def position_size(capital, risk_per_trade_pct, entry, stop):
    risk_amount = capital * (risk_per_trade_pct / 100)
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        return 0
    shares = int(risk_amount / risk_per_share)
    return shares
```

### Targets

- **Target 1:** Entry + (2 × risk_per_share). Standard 2:1 R:R.
- **Target 2 (measured move):** Entry + base_height. Where base_height is the height of the consolidation/pattern (top minus bottom).

## Indian Market Specifics

- **Trading hours:** 9:15 AM to 3:30 PM IST, Monday to Friday. Pre-open: 9:00–9:15. Post-close: nothing significant for our purposes.
- **Holidays:** NSE publishes annual holiday list. Fetch dynamically or hardcode annually. **Do not run jobs on holidays.** Check via NSE's holiday API or maintain a static `holidays_2026.txt`.
- **Settlement:** T+1 since January 2023. Not directly relevant to scanner logic but mention in README.
- **Circuit breakers:** Individual stocks have 2%, 5%, 10%, 20% price bands. A stock locked in upper circuit cannot be entered — flag this in alerts.
- **F&O expiry:** Last Thursday of the month. Volatility spikes around this. Optional: flag last Thursday alerts with a `near_expiry` note.
- **Earnings season:** Roughly mid-January, mid-April, mid-July, mid-October. Many stocks announce results during these windows. The earnings blackout filter is more aggressive during these months.
- **Promoter pledge data:** Available from BSE/NSE corporate filings. Use `nsepython` or scrape Screener.in if not available via your chosen data source.
- **Sector indices:** Use NIFTY Bank, NIFTY IT, NIFTY Auto, NIFTY Pharma, NIFTY FMCG, NIFTY Metal, NIFTY Realty, NIFTY Energy, NIFTY Financial Services. Map each NIFTY 500 stock to its sector via a maintained mapping table.

## SQLite Schema

```sql
-- Price cache (one row per ticker per trading day)
CREATE TABLE prices (
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX idx_prices_symbol ON prices(symbol);

-- Universe (NIFTY 500 membership, updated weekly)
CREATE TABLE universe (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    sector TEXT,
    industry TEXT,
    market_cap_cr REAL,
    last_updated DATE
);

-- Setup watchlist (refreshed each morning)
CREATE TABLE setup_watchlist (
    symbol TEXT PRIMARY KEY,
    pattern TEXT,
    breakout_level REAL,
    score REAL,
    detected_date DATE,
    base_height REAL,
    notes TEXT
);

-- Pullback watchlist (rolling window)
CREATE TABLE pullback_watchlist (
    symbol TEXT PRIMARY KEY,
    breakout_date DATE,
    breakout_level REAL,
    original_score REAL,
    notes TEXT
);

-- Paper trades (the audit log)
CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    pattern TEXT,
    alert_date DATE NOT NULL,
    alert_type TEXT,  -- 'BREAKOUT' or 'PULLBACK_ENTRY'
    score REAL,
    state TEXT NOT NULL,  -- ALERTED, ENTERED, TARGET_1_HIT, TARGET_HIT, STOPPED_OUT, TIME_EXIT, CANCELED
    entry_date DATE,
    entry_price REAL,
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    exit_date DATE,
    exit_price REAL,
    shares INTEGER,
    pnl_inr REAL,
    pnl_r REAL,  -- in R-multiples
    days_held INTEGER,
    max_favorable REAL,  -- highest unrealized profit during trade
    max_adverse REAL,    -- worst unrealized loss during trade
    notes TEXT
);

-- Failed breakouts (for tracking quality of signals over time)
CREATE TABLE failed_breakouts (
    symbol TEXT NOT NULL,
    breakout_date DATE NOT NULL,
    failure_date DATE NOT NULL,
    pattern TEXT,
    original_score REAL,
    PRIMARY KEY (symbol, breakout_date)
);

-- Run log
CREATE TABLE run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status TEXT,  -- SUCCESS, FAILED, PARTIAL
    alerts_generated INTEGER,
    error_message TEXT
);
```

## Build Phases

Build in three phases. After each phase, **stop and verify with the user** that the output looks sensible before continuing.

### Phase 1: Working MVP (target: ~500 lines)

Goal: A scanner that runs end-to-end and produces ranked candidates, even if the pattern coverage is partial.

Deliverables:

- Project structure, `pyproject.toml`, `config.yaml` from setup answers
- `data/universe.py` — Fetch and cache NIFTY 500 list (use NSE's official CSV at `https://archives.nseindia.com/content/indices/ind_nifty500list.csv`)
- `data/fetcher.py` — yfinance-cache OR Angel One wrapper per user choice. Batch fetching with retries and rate limit handling.
- `data/store.py` — SQLite schema creation, basic CRUD
- `analysis/indicators.py` — RSI, ATR, SMA, EMA, BB, volume MA
- `analysis/pivots.py` — Swing high/low detection with extensive tests
- `analysis/patterns.py` — Just 3 patterns: `detect_52w_high_breakout`, `detect_darvas_box`, `detect_nr7`
- `analysis/stage.py` — Weinstein classifier
- `filters/quality.py` — Market cap, liquidity, listing age checks
- `scoring.py` — Composite score
- `paper/tracker.py` — Basic state machine (just ALERTED → ENTERED → STOPPED_OUT/TARGET_HIT/TIME_EXIT)
- `jobs/morning_scan.py` — End-to-end morning scan
- `jobs/preclose_scan.py` — End-to-end pre-close scan
- `jobs/eod_settle.py` — Settle paper trades
- `output/alerts.py` — CSV output (Telegram/email deferred)
- `tests/test_pivots.py` — Critical, comprehensive
- `tests/test_patterns.py` — Basic positive and negative fixtures for the 3 patterns

After Phase 1: run the morning scan on the user's machine, show them the output, confirm it looks sensible. Validate that pivot detection is correct by visually inspecting 3–5 stocks on TradingView/Chartink.

### Phase 2: Confirmation layer

Add the remaining 6 patterns (`inside_bar`, `bollinger_squeeze`, `ascending_triangle`, `vcp`, `cup_and_handle`, `flag`).

Add `analysis/tightness.py`, `analysis/rs.py`, `filters/mood.py`, `filters/earnings.py`.

Implement the pullback watchlist logic in `morning_scan` and `preclose_scan`.

Improve scoring to use all signal sources.

Add `output/digest.py` for paper trade performance summaries.

Add Telegram/email output if user chose those.

### Phase 3: Validation and polish

Add a backtest harness that can replay any past N days against current logic and produce summary stats.

Add `paper/stats.py` for detailed analytics (win rate by pattern, by score band, by sector).

Add failed-breakout detection and emit `EXIT_SIGNAL` alerts for paper trades stopped within 3 days.

Add scheduler setup script (cron entries for Mac/Linux, Task Scheduler XML for Windows).

Polish logging, error handling, retry logic.

## Code Quality Standards

- **Type hints everywhere.** Use `from __future__ import annotations` and modern type syntax.
- **Docstrings on every public function**, with brief description, args, returns, and any non-obvious behavior.
- **Use dataclasses for structured returns**, not raw tuples or dicts.
- **No magic numbers.** All thresholds (`n=5` for pivots, `2%` for "near breakout", etc.) come from config or are named constants with comments explaining the choice.
- **Pure functions where possible.** Pattern detectors should be pure: take a DataFrame, return a PatternMatch. No global state, no I/O.
- **Tests for every pattern detector.** Especially pivots — the whole system depends on correct pivots.
- **Use `rich` for console output.** Tables for the alert list, colors for status (green for entered, red for stopped out, yellow for time exit).
- **Log everything to `logs/YYYY-MM-DD.log`.** Use `logging` module with INFO level by default, DEBUG when troubleshooting.
- **Fail loudly.** If yfinance returns empty for a ticker, log it and skip — don't silently produce garbage.

## Things NOT to Build

To keep scope focused, do **not** build:

- A web UI or dashboard (CSV + console + optional Telegram is enough)
- Real broker execution / order placement
- Options or F&O analysis (equity cash market only)
- Multi-timeframe analysis (daily only)
- ML / deep learning models
- Sentiment analysis from news (deferred to a possible Phase 4)
- Multi-user features

## Verification Checklist (Before Calling It Done)

Phase 1 is done when:

- [ ] `python -m breakout.jobs.morning_scan` runs end-to-end without errors
- [ ] Output CSV contains at least 5 candidates on a normal market day
- [ ] User has manually verified that 3 of those candidates actually look like setups on TradingView
- [ ] `pytest tests/` passes 100%
- [ ] Pivot detection has been validated against at least one hand-checked fixture
- [ ] Paper trades from Day 1 are recorded in SQLite

Phase 2 is done when:

- [ ] All 9 patterns detect on at least one historical example each
- [ ] Pullback watchlist correctly persists across days
- [ ] Tightness, RS, sector context all contribute to scoring
- [ ] Telegram or email output works if configured
- [ ] After 1 week of running, paper trade log has reasonable data

Phase 3 is done when:

- [ ] Backtest can replay last 60 days and produce a summary
- [ ] Win rate, profit factor, average R are computable
- [ ] Scheduler runs autonomously without manual intervention
- [ ] Failed breakouts emit exit signals for related paper trades

## What to Ask the User During the Build

In addition to the initial setup questions, ask the user when:

- A pattern detector produces too many or too few matches in initial testing (might need threshold tuning)
- The Stage 2 filter excludes more than 80% of the universe (might be a bear market — ask if they want to relax it)
- Any external API requires authentication you don't have (e.g., Angel One credentials)
- A piece of data isn't reliably available for free (might need a paid alternative or a fallback)

Never silently make assumptions about user preferences for thresholds, weights, or scope changes. Default to the values in this spec, surface anything ambiguous.

## Final Note

The user is genuinely engaged in this project and has good market knowledge — treat them as a capable collaborator. Show them tables of outputs, ask their opinion on borderline pattern matches, suggest visualizations on TradingView for them to sanity-check the system. The goal is for them to *trust* the scanner before they trade real capital with it, and trust is built through transparency, testability, and the paper trading record.

Build well. Test thoroughly. Good luck.
