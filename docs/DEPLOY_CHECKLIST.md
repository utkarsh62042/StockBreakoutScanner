# DEPLOY CHECKLIST

## Pre-Deployment (Before Paper Trading)

### ✅ Code Changes Verified
- [x] Issue 1.1: Entry timing clarified (3:25 PM IST)
- [x] Issue 1.3: Pre-close scan timing (3:20 PM IST)
- [x] Issue 2.1: Portfolio circuit breaker + FII/DII scaling
- [x] Issue 3.3: All 9 patterns enabled
- [x] Config structure updated (new risk fields)
- [x] README updated (timing, patterns, FII flow)

### ✅ New Files Created
- [x] `breakout/filters/portfolio.py` — portfolio risk gates
- [x] `FIXES_APPLIED.md` — detailed explanation of all fixes
- [x] `ACTION_SUMMARY.md` — quick reference guide
- [x] `CONFIGURATION_GUIDE.md` — step-by-step setup instructions
- [x] `DEPLOY_CHECKLIST.md` — this file

### ✅ Documentation Updated
- [x] `README.md` — timing, patterns, FII/DII section
- [x] `config.example.yaml` — new portfolio-level limits
- [x] `breakout/config.py` — RiskConfig new fields
- [x] `tracker.py` — entry timing documentation
- [x] `preclose_scan.py` — timing and stress scaling
- [x] `morning_scan.py` — portfolio health check

---

## Configuration Steps (30 minutes)

```bash
# 1. Copy template
cp config.example.yaml config.yaml

# 2. Edit config.yaml:
# - Set capital = YOUR AMOUNT
# - Set risk_per_trade_pct = YOUR PREFERENCE
# - Verify all 9 patterns enabled
# - Check FII/DII portfolio limits
```

**Config Checklist:**
- [ ] `capital` set to your amount (₹500k, ₹1M, etc.)
- [ ] `risk_per_trade_pct` set (1.0% recommended for start)
- [ ] `max_concurrent_positions` set (8 is good baseline)
- [ ] `max_portfolio_loss_pct_daily: 3.0` (OK as-is)
- [ ] `max_portfolio_loss_pct_monthly: 10.0` (OK as-is)
- [ ] All 9 patterns enabled in `patterns.enabled`
- [ ] `regime.enabled: true` (for FIIflow awareness)

---

## Deployment Steps

### Step 1: First Run — Morning Scan (9:30 AM)
```bash
python -m breakout.jobs.morning_scan
```
**Verify:**
- [ ] Completes without error
- [ ] Logs show "morning_scan complete: X symbols on setup watchlist"
- [ ] Logs show portfolio health status
- [ ] Logs show patterns across all 9 types

### Step 2: Pre-close Scan (3:20 PM)
```bash
python -m breakout.jobs.preclose_scan
```
**Verify:**
- [ ] Completes without error
- [ ] Generates alerts (BREAKOUT and/or PULLBACK)
- [ ] Entry prices reasonable (close to market at 3:20-3:25 PM)
- [ ] Position sizes reasonable (not too large, not zero)

### Step 3: EOD Settle (3:40 PM or 4:00 PM)
```bash
python -m breakout.jobs.eod_settle
```
**Verify:**
- [ ] Settled paper trades are closed/updated
- [ ] No errors in logs

---

## Testing (Before Live Trading)

### Test 1: Timing ✓
- [ ] Pre-close scan at 3:20 PM
- [ ] Entry price ≈ 3:20-3:25 PM market price
- [ ] NOT 3:00 PM price
- [ ] NOT next-day open price

### Test 2: All Patterns ✓
- [ ] Run morning scan
- [ ] Check logs for all 9 pattern names
- [ ] Watchlist includes entries from all patterns

### Test 3: Circuit Breaker ✓
- [ ] Manually insert trade with -3.5% loss
- [ ] Run morning scan
- [ ] Verify it aborts with "portfolio in distress"

### Test 4: Stress Scaling ✓
- [ ] Set VIX = 35
- [ ] Run pre-close scan
- [ ] Position sizes should be 25% of normal
- [ ] Logs should show "market stress scaling"

### Test 5: Config Loading ✓
- [ ] No errors when loading config.yaml
- [ ] All new fields read correctly

---

## Ongoing Operations

### Daily Rhythm
```
09:30 AM → Morning Scan
  └─ Identifies setups
  └─ Checks portfolio health
  
15:20 PM → Pre-close Scan
  └─ Confirms breakouts
  └─ Applies stress scaling
  └─ Opens paper positions
  
16:00 PM → EOD Settle
  └─ Updates paper trades
  └─ Records outcomes
```

### Weekly Check
```
Friday EOD → Review weekly performance
  └─ Check win rate by pattern
  └─ Review portfolio stress scaling effectiveness
  └─ Plan next week entries
```

### Monthly Reset
```
End of month → Review monthly P&L
  └─ If down >10%: STOP trading (circuit breaker)
  └─ Review patterns vs outcomes
  └─ Adjust config if needed
  └─ Reset for next month
```

---

## Circuit Breaker Triggers (⚠️ WATCH THESE)

### ❌ DO NOT IGNORE
If morning scan shows these messages, **STOP opening new positions**:

```
ERROR: portfolio in distress — aborting scan.
Daily loss ₹18000 (3.60%) exceeds limit 3.0%
```
→ **Action:** Do not trade for rest of day. Settle existing positions only.

```
ERROR: portfolio in distress — aborting scan.
Monthly loss ₹55000 (11.0%) exceeds limit 10.0%
```
→ **Action:** STOP trading. Reset for next month. Review what went wrong.

```
WARNING: market stress scaling active: position sizes scaled to 25%
(VIX=35, breadth=25%)
```
→ **Action:** OK to trade, but sizes are reduced. Continue monitoring.

---

## Monitoring Commands

### Check today's portfolio status
```bash
grep "portfolio health\|portfolio in distress" logs/*.log | tail -5
```

### Check recent alerts
```bash
tail -10 output/alerts_*.csv
```

### Check open paper positions
```bash
tail -20 data_cache/breakout.xlsx  # View paper_trades sheet
```

### Check VIX level for the day
```bash
grep "VIX=" logs/*.log | tail -1
```

---

## Troubleshooting

### "Config loading failed"
→ Check YAML syntax (indentation, spaces not tabs)

### "No setup_watchlist entries"
→ Normal if market conditions bad or patterns weak
→ Check logs for skip reasons

### "Paper trading showing 0 shares"
→ Position size calculation issue
→ Check: entry price vs stop distance
→ May need to adjust capital or risk settings

### "Circuit breaker triggering too often"
→ Adjust limits: increase `max_portfolio_loss_pct_daily` from 3.0 to 4-5%

### "No alerts generated at 3:20 PM"
→ No setups confirmed the breakout
→ Check watchlist had entries (morning scan)
→ Check volume and price conditions

---

## Go-Live Criteria (Before Real Money)

✅ **Paper trading for 4-6 weeks** minimum
✅ **50+ paper trades** accumulated
✅ **Win rate > 50%**
✅ **Average R-multiple > +0.5R**
✅ **Profit factor > 1.5**
✅ **Circuit breaker tested and working**
✅ **Stress scaling verified on high-VIX days**

⏳ **Still Pending Before Live Trading:**
- [ ] Issue 2.2: Gap-down slippage modeling
- [ ] Issue 4.1: Earnings filter fix (use NSE API)
- [ ] Issue 5.1: Universe refresh reduction (7→2 days)

---

## Quick Links

- **Setup Guide:** [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md)
- **Detailed Fixes:** [FIXES_APPLIED.md](FIXES_APPLIED.md)
- **Quick Summary:** [ACTION_SUMMARY.md](ACTION_SUMMARY.md)
- **Audit Report:** [SeniorAnalyst.md](SeniorAnalyst.md)
- **System Design:** [README.md](README.md)

---

## Support

**Issues with timing?** → See [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md) Section 2, Test 2

**Issues with patterns?** → See [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md) Section 2, Test 3

**Issues with circuit breaker?** → See [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md) Section 2, Test 4

**Questions about stress scaling?** → See [ACTION_SUMMARY.md](ACTION_SUMMARY.md) "If VIX = 35, Breadth = 25%"

---

**Status:** ✅ READY FOR PAPER TRADING  
**Date:** 2026-09-23  
**Fixes Applied:** 6 of 12 issues (50% complete)  
**Critical Remaining:** 3 issues (for live trading only)

**Next Step:** Copy config.example.yaml → config.yaml, then run morning_scan at 9:30 AM tomorrow!
