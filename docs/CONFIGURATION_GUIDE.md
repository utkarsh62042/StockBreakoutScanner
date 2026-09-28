# Configuration & Testing Guide

**For:** FII/DII-Aware Entry Timing + All Pattern Enablement  
**Version:** Post-fixes 2026-09-23

---

## SECTION 1: Configuration Setup

### Step 1: Copy and Customize config.yaml

```bash
cp config.example.yaml config.yaml
```

### Step 2: Set Your Risk Parameters

Edit `config.yaml`:

```yaml
risk:
  capital: 500000                    # YOUR CAPITAL HERE
  risk_per_trade_pct: 1.0            # Risk per trade (%)
  max_concurrent_positions: 8        # Max open positions
  max_positions_per_sector: 3        # Sector concentration limit
  
  # 🆕 PORTFOLIO CIRCUIT BREAKERS (NEW)
  max_portfolio_loss_pct_daily: 3.0      # Stop if down >3% today (FII panic)
  max_portfolio_loss_pct_monthly: 10.0   # Stop if down >10% month-to-date
  
  # 🆕 MARKET STRESS SCALING (NEW)
  scale_positions_in_market_stress: true # Auto-scale in high VIX or narrow breadth
```

### Step 3: Verify All Patterns Are Enabled

In `config.yaml`, check patterns section:

```yaml
patterns:
  enabled:
    - fifty_two_week_high        # Always on
    - darvas_box                 # Always on
    - nr7                        # Always on
    - inside_bar                 # Always on
    - bollinger_squeeze          # Always on
    - ascending_triangle         # Always on
    - vcp                        # Always on
    - cup_and_handle             # Always on
    - flag                       # Always on
```

All 9 should be enabled. If you want to test individually, comment out patterns:

```yaml
patterns:
  enabled:
    # - fifty_two_week_high      # Temporarily disabled to test others
    - darvas_box
    - nr7
    # ... etc
```

### Step 4: Review Other Settings

```yaml
# Morning scan should run at 9:30 AM IST
# Pre-close scan should run at 3:20 PM IST (was 3:00 PM before)

# VIX thresholds for market mood
regime:
  enabled: true                  # Market regime gate
  nifty_symbol: "^NSEI"         # NIFTY index
  nifty_sma: 200                # 200-day SMA
  breadth_sma: 50               # Market breadth SMA
  breadth_up_pct: 60            # % threshold for bullish breadth
  breadth_down_pct: 40          # % threshold for bearish breadth
  risk_off_multiplier: 0.5      # Scale positions to 50% in risk-off
  severe_multiplier: 0.25       # Scale to 25% in severe risk-off

# Costs and slippage (realistic for NSE delivery)
costs:
  enabled: true
  brokerage_pct: 0.1            # 10 bps brokerage
  stt_pct: 0.1                  # 10 bps STT
  slippage_pct: 0.05            # 5 bps execution slippage
```

---

## SECTION 2: Testing Checklist

### ✅ Test 1: Configuration Loading

**Goal:** Ensure config.yaml loads without errors

**Steps:**
```python
# In Python REPL or script
from breakout.config import load_config

cfg = load_config()
print(f"Capital: ₹{cfg.risk.capital}")
print(f"Risk per trade: {cfg.risk.risk_per_trade_pct}%")
print(f"Daily loss limit: {cfg.risk.max_portfolio_loss_pct_daily}%")
print(f"Patterns enabled: {cfg.patterns.enabled}")
print("✓ Config loaded successfully")
```

**Expected Output:**
```
Capital: ₹500000
Risk per trade: 1.0%
Daily loss limit: 3.0%
Patterns enabled: ['fifty_two_week_high', 'darvas_box', ...]
✓ Config loaded successfully
```

---

### ✅ Test 2: Timing — Entry at 3:25 PM

**Goal:** Verify entry price is at 3:20-3:25 PM (not next-day open)

**Setup:**
1. Get a stock that's breaking out (e.g., RELIANCE)
2. Get market price at 3:25 PM IST
3. Run paper trade entry
4. Compare: paper entry_price should ≈ market price at 3:25 PM (±0.5%)

**Verification:**
```python
# After running pre-close scan at 3:20 PM
import pandas as pd
from breakout.data.store import Store

store = Store("data_cache/breakout.xlsx")
trades = store.read_paper_trades_by_state("ENTERED")

for trade in trades:
    entry = trade['entry_price']
    date = trade['entry_date']
    symbol = trade['symbol']
    print(f"{symbol} | Entry: ₹{entry} | Date: {date}")
    print(f"  This should be close to market price at 3:20-3:25 PM")
```

**Success Criteria:**
- Entry date = alert date (same day, not next day)
- Entry time ≈ 3:25 PM IST (based on pre-close scan confirming at 3:20 PM)
- Entry price ≈ market close at 3:20-3:25 PM (not 3:00 PM, not next day open)

---

### ✅ Test 3: All Patterns Enabled

**Goal:** Verify all 9 patterns are being detected

**Setup:**
```bash
# Run morning scan
python -m breakout.jobs.morning_scan

# Check logs
tail -100 logs/morning_scan_*.log | grep "pattern"
```

**Expected Output (should see all 9 pattern names):**
```
symbol=RELIANCE | pattern=fifty_two_week_high | confidence=85.0
symbol=TCS | pattern=darvas_box | confidence=70.0
symbol=INFY | pattern=cup_and_handle | confidence=65.0
symbol=WIPRO | pattern=ascending_triangle | confidence=72.0
symbol=SBIN | pattern=vcp | confidence=68.0
symbol=HDFC | pattern=bollinger_squeeze | confidence=75.0
symbol=MARUTI | pattern=nr7 | confidence=70.0
symbol=SUNPHARMA | pattern=flag | confidence=60.0
symbol=BAJAJFINSV | pattern=inside_bar | confidence=62.0
```

**Verification:**
```python
# In Python
from breakout.analysis.patterns import PATTERN_REGISTRY

print(f"Total patterns registered: {len(PATTERN_REGISTRY)}")
for name in PATTERN_REGISTRY:
    print(f"  ✓ {name}")

# Should print 9 patterns
```

**Success Criteria:**
- 9 patterns registered (not just 3)
- All patterns found in logs
- watchlist includes setups for all pattern types

---

### ✅ Test 4: Portfolio Circuit Breaker

**Goal:** Verify system stops trading when portfolio is down >3% today

**Setup:**

Option A (Manual):
1. Open `data_cache/breakout.xlsx`
2. Go to `paper_trades` sheet
3. Manually insert a trade with `pnl_inr = -18000` (3.6% loss on 500k) and `exit_date = today`
4. Save and close

Option B (Programmatic):
```python
from breakout.data.store import Store
from datetime import datetime

store = Store("data_cache/breakout.xlsx")

# Insert a losing trade for today
store.insert_paper_trade({
    "symbol": "TEST",
    "pattern": "test_breaker",
    "state": "STOPPED_OUT",
    "entry_date": "2026-09-23",
    "entry_price": 100.0,
    "exit_date": "2026-09-23",  # Same day
    "exit_price": 96.4,
    "shares": 100,
    "pnl_inr": -18000,  # 3.6% loss
})
store.close()
```

**Run Test:**
```bash
python -m breakout.jobs.morning_scan
```

**Expected Output:**
```
ERROR: portfolio in distress — aborting scan.
Daily loss ₹18000 (3.60%) exceeds limit 3.0%
Reasons: Daily loss ₹18000 (3.60%) exceeds limit 3.0%
```

**Success Criteria:**
- Morning scan aborts (returns 0)
- Error message mentions portfolio distress
- No new alerts generated
- Log shows "ABORTED_PORTFOLIO_DISTRESS" in run_log

---

### ✅ Test 5: Market Stress Scaling

**Goal:** Verify position sizes scale down in high-VIX environments

**Setup:**

Simulate high VIX by:
1. Run morning scan normally
2. Check watchlist rows for VIX value
3. Run pre-close scan and note position sizes

Compare:
- Low VIX day (20): position sizes = normal
- High VIX day (35): position sizes should be 25% of normal

**Verification:**
```python
from breakout.data.store import Store

store = Store("data_cache/breakout.xlsx")

# Get recent trades
trades = store.read_paper_trades_by_state("ENTERED")

for trade in trades[-5:]:
    risk = trade['entry_price'] - trade['stop_loss']
    capital = 500000
    expected_shares_full = (capital * 0.01) / risk  # Full size
    actual_shares = trade['shares']
    
    multiplier = actual_shares / expected_shares_full
    print(f"{trade['symbol']}: {actual_shares} shares (multiplier: {multiplier:.2f}x)")
    if multiplier < 1.0:
        print(f"  ✓ Scaled down by {(1-multiplier)*100:.0f}%")
```

**Expected Output (on high-VIX day):**
```
RELIANCE: 250 shares (multiplier: 0.25x)
  ✓ Scaled down by 75%
TCS: 500 shares (multiplier: 0.50x)
  ✓ Scaled down by 50%
```

**Success Criteria:**
- Position sizes smaller than baseline on high-VIX days
- Scaling factor matches VIX level:
  - VIX 35 → 0.25x (25% of normal)
  - VIX 28 → 0.50x (50% of normal)
  - Breadth < 30% also triggers scaling

---

### ✅ Test 6: Monthly Circuit Breaker

**Goal:** Verify system stops if down >10% month-to-date

**Setup:**
```python
# Manually insert multiple losing trades for this month
from breakout.data.store import Store
from datetime import datetime, timedelta

store = Store("data_cache/breakout.xlsx")
today = datetime.now().date()
month_start = today.replace(day=1)

# Insert 5 trades totaling -11% loss
for i in range(5):
    exit_date = month_start + timedelta(days=i*5)
    store.insert_paper_trade({
        "symbol": f"TEST{i}",
        "pattern": "test",
        "state": "STOPPED_OUT",
        "entry_date": str(exit_date - timedelta(days=1)),
        "entry_price": 100.0,
        "exit_date": str(exit_date),
        "exit_price": 89.0,  # 11% loss
        "shares": 100,
        "pnl_inr": -55000,  # This will accumulate
    })
store.close()
```

**Run Test:**
```bash
python -m breakout.jobs.morning_scan
```

**Expected Output:**
```
ERROR: portfolio in distress — aborting scan.
Monthly loss ₹275000 (55.00%) exceeds limit 10.0%
```

**Success Criteria:**
- Morning scan aborts
- Shows monthly loss percentage
- Stops new positions until next month

---

## SECTION 3: First Paper Trade Run

### Day 1 (Thursday Morning)

1. **Setup:**
   ```bash
   cp config.example.yaml config.yaml
   # Edit config.yaml: set capital=YOUR_AMOUNT
   ```

2. **Run 9:30 AM scan:**
   ```bash
   python -m breakout.jobs.morning_scan
   ```

3. **Verify output:**
   - Logs should show "morning_scan complete: X symbols on setup watchlist"
   - Check watchlist for patterns across all 9 types
   - Verify portfolio health is OK

### Day 1 (Thursday 3:20 PM)

1. **Run pre-close scan:**
   ```bash
   python -m breakout.jobs.preclose_scan
   ```

2. **Review output:**
   - Should show "Pre-close alerts" with BREAKOUT and PULLBACK entries
   - Check entry_price matches market price at 3:20-3:25 PM
   - Position sizes should reflect market conditions (normal or stress-scaled)

### Day 2+ (Ongoing)

1. **Daily rhythm:**
   - 9:30 AM: Run morning scan
   - 3:20 PM: Run pre-close scan
   - 4:00 PM: Run EOD settle (settle paper trades)
   - Review alerts and paper positions

2. **Monitor:**
   ```bash
   # Check alerts
   tail -20 output/alerts_*.csv
   
   # Check paper trades
   python -c "
   from breakout.data.store import Store
   store = Store('data_cache/breakout.xlsx')
   trades = store.read_paper_trades_by_state('ENTERED', 'TARGET_1_HIT')
   for t in trades[-3:]:
       print(f\"{t['symbol']}: ₹{t['entry_price']} -> Stop ₹{t['stop_loss']}\")
   "
   
   # Check portfolio health
   grep "portfolio health\|portfolio in distress" logs/*.log | tail -10
   ```

---

## SECTION 4: Validation Metrics (After 4-6 Weeks)

Once you have 50+ paper trades, review:

### Pattern Performance
```
Pattern          | Signals | Win% | Avg R
52w-high         | 15      | 55%  | +0.8R
Darvas box       | 12      | 60%  | +0.9R
Cup & handle     | 8       | 62%  | +1.2R
VCP              | 6       | 50%  | +0.6R
... (all 9)
```

### Market Condition Performance
```
Condition                    | Win% | Avg R
Normal (VIX 15-20)          | 58%  | +0.9R
Elevated (VIX 20-25)        | 52%  | +0.7R
Stressed (VIX > 25)         | 48%  | +0.5R
  → With stress scaling      | 55%  | +0.7R  ✓ Improved
```

### Overall Metrics
```
Total Trades:      68
Win Rate:          54%
Average R:         +0.82R
Profit Factor:     1.8
Max Drawdown:      -8.2%
Sharpe Ratio:      0.65
```

**Success Thresholds:**
- ✅ Win rate > 50%
- ✅ Average R > +0.5R
- ✅ Profit factor > 1.5
- ✅ Max drawdown < 15%

---

## SECTION 5: Common Issues & Fixes

### Issue: "Config loading failed" error

**Fix:**
```bash
# Verify config syntax
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Should print nothing if valid
# If invalid, check YAML indentation (spaces, not tabs)
```

### Issue: Portfolio breaker triggers too often

**Adjustment:**
```yaml
risk:
  max_portfolio_loss_pct_daily: 5.0   # Increase from 3.0 to 5.0
  max_portfolio_loss_pct_monthly: 15.0  # Increase from 10.0 to 15.0
```

### Issue: Stress scaling too aggressive (positions too small)

**Adjustment:**
```python
# In filters/portfolio.py, function should_scale_position_for_market_stress
# Modify VIX thresholds
if vix >= 40.0:  # Was 30.0
    multipliers.append(0.25)
elif vix >= 30.0:  # Was 25.0
    multipliers.append(0.50)
```

### Issue: Missing patterns in watchlist

**Verification:**
```bash
# Check if patterns are enabled
grep "enabled:" config.yaml -A 10

# Should list all 9 patterns (none commented out)
# If any are missing, add them back
```

---

**Ready to deploy:** YES ✅  
**Recommended paper trading period:** 4-6 weeks  
**Before going live:** Fix issues 2.2, 4.1, 5.1 from SeniorAnalyst.md
