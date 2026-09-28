# Indian Stock Breakout Scanner

A local Python system that scans the NIFTY 500 universe twice daily to identify high-probability breakout setups on the Indian stock market. Designed for swing traders holding positions from a few days to a few weeks.

> **Disclaimer:** This is an educational/personal tool. It is **not** SEBI-registered investment advice. All trading involves risk of capital loss. Past patterns do not guarantee future returns. Paper-trade for at least 4–6 weeks before risking real capital, and always do your own due diligence.

---

## What this system does

Every trading day it runs two batch jobs on your laptop:

1. **Morning scan (9:30 AM IST)** — Scans all NIFTY 500 stocks, identifies setups that are *close to* breaking out, and updates two watchlists: stocks setting up for fresh breakouts, and stocks that recently broke out and could provide pullback entries.

2. **Pre-close scan (3:20 PM IST)** — Re-checks the watchlists in the final 10 minutes of the trading session (gives you time to analyze the 3:00 PM market action). Confirms which setups are actually closing as breakout candles (the "smart money hour" effect), and generates alerts with precise entry, stop-loss, and target levels.

Why end-of-day? Closing breakouts have a meaningfully higher success rate than intraday breakouts because institutional commitment shows up in the close, and the 3:00–3:25 PM window filters out the false breakouts that get sold off earlier in the day.

---

## The trading strategy in one screen

The system looks for stocks that satisfy **all** of these conditions:

**Stage filter (hard pass/fail).** Only Stage 2 stocks per Stan Weinstein's framework — price above a rising 30-week (150-day) simple moving average. This single filter eliminates roughly 70% of false breakouts.

**Quality floor (hard pass/fail).** Market cap above ₹500 cr, average daily turnover above ₹5 cr, promoter pledge below 30%, listed for at least one year. Removes manipulated penny stocks before pattern detection runs.

**Pattern detection (scored).** Detects all nine chart patterns:
  - 52-week high breakouts (with resistance touch count for confidence)
  - Darvas Boxes (parallel support and resistance)
  - NR7 (narrow range compression breakouts)
  - Inside Bars (range contraction into breakout)
  - Bollinger Squeeze (volatility compression)
  - Ascending Triangles (bullish accumulation)
  - Volatility Contraction Patterns (VCP)
  - Cup & Handle formations
  - Flag patterns (short-term consolidation)

Each pattern returns a confidence score. All patterns are enabled by default.

**Confirmation signals (scored).** Volume on the breakout candle must be at least 1.5× the 20-day average. Pre-breakout tightness (contracting ATR) adds points. Relative strength versus NIFTY adds points. Sector index trending up adds points.

**Earnings blackout (hard pass/fail).** Exclude stocks within ±5 trading days of an earnings announcement, unless the breakout *is* the post-earnings reaction.

A composite score from 0 to 100 ranks the survivors. The system also tracks failed breakouts (stocks that closed back below their breakout level within three days) and flags them as exit signals.

---

## What you need from your end

Before running, you'll need to provide a few things. The build script will ask you these as setup questions; this section is for your own reference.

**1. Python environment.** Python 3.10 or newer on Windows/Mac/Linux. The build will use `uv` (recommended) or `pip` for dependency management. RAM requirement is under 1 GB — your old laptop is fine.

**2. Data source choice.** You have three options, in order of preference:

| Source | Cost | Reliability | What you need |
|--------|------|-------------|---------------|
| Angel One SmartAPI | Free | High | An Angel One demat account + API key |
| yfinance + yfinance-cache | Free | Medium | Nothing — works out of the box |
| Upstox API | Free | High | An Upstox demat account + API key |

If you already have an Angel One or Upstox demat, use that — it's more reliable than yfinance, which occasionally rate-limits aggressive users. If you don't have either, yfinance with proper caching works well for our two-runs-per-day usage pattern.

**3. Output preference.** Three options:

- **CSV only** — Results written to `output/alerts_YYYY-MM-DD.csv`. Simplest.
- **CSV + Telegram bot** — Real-time alerts pushed to your phone. Needs a Telegram bot token (free, takes 5 minutes to set up).
- **CSV + Email** — Daily digest emailed to you. Needs an SMTP-enabled email (Gmail app password works).

**4. Risk parameters.** Capital you're willing to risk per trade. The system uses this to calculate position size based on the ATR-derived stop-loss distance, ensuring you never risk more than your specified percentage on a single trade. Default is 1% risk per trade, but you can configure it.

**5. Scheduling.** The system needs to run at 9:30 AM and 3:00 PM IST on trading days. The build will set this up via:

- **macOS/Linux:** `cron` entries in your crontab.
- **Windows:** Task Scheduler entries.

If your laptop is asleep at those times, the system will run on the next wake-up. You can also trigger runs manually.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ DATA LAYER                                                  │
│ Universe (NIFTY 500) → Fetcher (cached) → Excel workbook    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ ANALYSIS LAYER                                              │
│ Indicators · Pivot detector · Pattern detectors             │
│ Stage classifier · Tightness · Relative strength            │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ FILTER LAYER (hard pass/fail)                               │
│ Quality floor · Earnings blackout · Market mood gate        │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ SCORING ENGINE                                              │
│ Pattern (35) + Stage (20) + RS (15) + Volume (15)           │
│ + Tightness (10) + Sector (5) = Composite score (0–100)     │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ OUTPUT LAYER                                                │
│ Ranked alerts (CSV / Telegram / email)                      │
│ Paper trading log + outcome tracking                        │
└─────────────────────────────────────────────────────────────┘
```

---

## File structure

```
breakout_scanner/
├── README.md                # This file
├── pyproject.toml           # Dependencies
├── config.yaml              # Your settings (gitignored)
├── config.example.yaml      # Template to copy from
├── .gitignore
│
├── breakout/                # The package
│   ├── __init__.py
│   ├── config.py            # Loads config.yaml
│   │
│   ├── data/
│   │   ├── fetcher.py       # yfinance-cache / Angel One wrapper
│   │   ├── universe.py      # NIFTY 500 ticker list (auto-refresh weekly)
│   │   └── store.py         # Excel workbook (price cache, watchlists, paper log)
│   │
│   ├── analysis/
│   │   ├── indicators.py    # RSI, MACD, ATR, ADX, BB, MAs
│   │   ├── pivots.py        # Swing high/low detection (the primitive)
│   │   ├── patterns.py      # All nine chart pattern detectors
│   │   ├── stage.py         # Weinstein stage classifier
│   │   ├── tightness.py     # VCP / ATR contraction measurement
│   │   └── rs.py            # Relative strength vs NIFTY 500
│   │
│   ├── filters/
│   │   ├── quality.py       # Liquidity, market cap, pledge
│   │   ├── mood.py          # India VIX, sector index context
│   │   └── earnings.py      # Earnings blackout window
│   │
│   ├── scoring.py           # Composite score
│   │
│   ├── paper/
│   │   ├── tracker.py       # Paper trade lifecycle state machine
│   │   └── stats.py         # Win rate, R-multiples, profit factor
│   │
│   ├── jobs/
│   │   ├── morning_scan.py  # Entry point for 9:30 AM run
│   │   ├── preclose_scan.py # Entry point for 3:00 PM run
│   │   └── eod_settle.py    # After-market: settle paper trades
│   │
│   └── output/
│       ├── alerts.py        # Format and dispatch alerts
│       └── digest.py        # Daily paper-trade performance digest
│
├── data_cache/              # Excel workbook + cached OHLCV (gitignored)
├── logs/                    # Run logs (gitignored)
├── output/                  # CSV alert files (gitignored)
│
└── tests/
    ├── test_pivots.py       # Critical — pattern detection depends on this
    ├── test_patterns.py
    ├── test_indicators.py
    └── fixtures/             # Sample OHLCV data for known-good patterns
```

---

## How to run

After setup completes (the build process handles installation):

```bash
# Manual one-off scan
python -m breakout.jobs.morning_scan
python -m breakout.jobs.preclose_scan

# View today's alerts
cat output/alerts_$(date +%Y-%m-%d).csv

# View paper trading performance
python -m breakout.paper.stats --period 30d

# Reset paper trading log (use carefully)
python -m breakout.paper.tracker --reset
```

Scheduled runs happen automatically via cron / Task Scheduler once configured.

---

## Output format

Each alert row contains:

| Field | Description |
|-------|-------------|
| `symbol` | NSE ticker (e.g., `RELIANCE`) |
| `score` | Composite 0–100 |
| `pattern` | Detected pattern (e.g., `darvas_box`, `cup_and_handle`) |
| `breakout_level` | Price the stock needs to close above |
| `entry_price` | Suggested entry (typically the closing price) |
| `stop_loss` | 1.5× ATR below breakout level |
| `target_1` | 2:1 R:R target |
| `target_2` | Measured-move target (base height projected up) |
| `position_size` | Calculated from your risk-per-trade setting |
| `volume_ratio` | Today's volume / 20-day avg |
| `stage` | Weinstein stage (only Stage 2 alerts emitted) |
| `rs_rank` | Relative strength rank vs NIFTY 500 (1–100) |
| `tightness` | Pre-breakout volatility contraction score |
| `sector` | Sector index trend (up/flat/down) |
| `notes` | Any flags (e.g., `near_earnings`, `failed_recent_breakout`) |

The CSV is sorted by score descending.

---

## Paper trading

The paper trading system runs from day one. Every alert is automatically logged as a virtual position:

- **State machine:** `ALERTED → ENTERED → (TARGET_HIT | STOPPED_OUT | TIME_EXIT | CANCELED)`
- **Entry:** At 3:25 PM IST on the confirmation day (5 minutes after pre-close scan confirms), matching your real execution window of reviewing the alert and entering before market close at 3:30 PM.
- **Stop loss:** 1.5× ATR below breakout level. If intraday low breaches it, position is closed at the stop price.
- **Targets:** Two targets — 2:1 R:R and measured-move. System logs which one hit first.
- **Time exit:** If neither stop nor target hits within 30 trading days, position closes at that day's close.
- **Outcome tracking:** Every closed trade records win/loss, R-multiple, days held, max favorable excursion, max adverse excursion.

After 4–6 weeks you'll have enough data to see:

- Win rate overall and by pattern type
- Average R-multiple (target: above +0.5R)
- Profit factor (target: above 1.5)
- Which patterns work best in current market conditions
- Whether your scoring weights need adjustment

The system generates a weekly digest highlighting these metrics.

---

## Configuration

All settings live in `config.yaml`. The most important ones:

```yaml
universe: "nifty500"  # or "nifty200", "fno", "custom"

data_source: "yfinance"  # or "angelone", "upstox"

risk:
  capital: 500000             # Your trading capital in INR
  risk_per_trade_pct: 1.0     # % of capital risked per trade
  max_concurrent_positions: 8

scoring_weights:
  pattern_quality: 35
  stage: 20
  relative_strength: 15
  volume: 15
  tightness: 10
  sector: 5

thresholds:
  min_score_to_alert: 60      # Only emit alerts above this
  min_volume_ratio: 1.5       # Breakout candle min volume vs 20-day avg
  min_market_cap_cr: 500
  min_adv_cr: 5               # Average daily turnover

output:
  csv: true
  telegram: false
  telegram_bot_token: ""
  telegram_chat_id: ""
  email: false
  email_smtp: ""

paper_trading:
  enabled: true
  hold_max_days: 30
```

---

## FII/DII Flow Awareness & Market Stress Handling

The system is aware of FII/DII activity via:

- **India VIX monitoring** — Elevated VIX (>25) signals FII selling stress; position sizes scale down automatically
- **Market breadth tracking** — % of NIFTY 500 above their 50-day MA shows participation. Narrow breadth (<40%) triggers position size reduction
- **Sector momentum correlation** — Breakouts in risk-off regimes fail more often; sizing is automatically adjusted
- **Portfolio circuit breaker** — Stops opening new positions if:
  - Down >3% on the day (FII selling cascade)
  - Down >10% month-to-date (monthly reset discipline)
  - Concurrent position limit reached

This prevents the system from compounding losses during FII selling panics or regime reversals.

## Limitations and known issues

- **No intraday data dependency** — by design. The system makes decisions on daily OHLCV closes.
- **Earnings data is best-effort** — pulled from public sources, occasionally missing for smaller stocks.
- **yfinance can rate-limit** — the `yfinance-cache` wrapper mitigates this; switch to Angel One if it becomes a problem.
- **Backtest results are not predictive** — Indian market regime shifts (election cycles, RBI policy, FII flows) can dramatically change pattern reliability.
- **The system does not execute trades** — it only produces alerts. Execution is manual via your broker app.
- **FII/DII data is indirect** — VIX and breadth are proxies for flow direction; for real flow data, integrate FII Tracker API.

---

## Tuning your strategy

After 30+ paper-traded alerts you can start tuning:

1. **Look at win rate by pattern.** Disable patterns with below-50% win rates by setting their weight contribution to zero in `scoring_weights`.
2. **Look at win rate by score band.** If alerts scoring 60–70 have a low win rate, raise `min_score_to_alert` to 70.
3. **Look at win rate by sector context.** If breakouts in flat sectors lose more often than expected, increase the `sector` weight.
4. **Look at average R.** If average R is positive but win rate is low, you have a trend-following edge — let winners run by widening the target. If win rate is high but R is small, your targets are too conservative.

Re-tune every quarter or after any major market regime change.
