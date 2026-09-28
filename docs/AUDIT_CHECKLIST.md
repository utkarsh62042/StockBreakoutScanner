# System Audit Checklist — 2026-09-28

## 🎯 Quick Summary
**Good news:** System is production-ready. All critical functionality is implemented. This checklist shows what's verified and what needs attention.

---

## ✅ Items Verified (WORKING CORRECTLY)

- [x] **All 9 chart patterns implemented** (inside_bar, bollinger_squeeze, ascending_triangle, vcp, cup_and_handle, flag, and the original three)
- [x] **Position sizing with capital cap** — Fixed 2026-09-12, now caps at ₹62,500/position (₹500k÷8 slots)
- [x] **Extension penalty applied** — Chase penalty kicks in at 2%, fully penalized by 8%
- [x] **Pullback retest validation** — Requires explicit touch AND strong close (50% range OR 1% above level)
- [x] **Market regime gate** — 3-vote system (NIFTY trend, breadth, VIX) scales position size 0.25–1.0×
- [x] **Earnings blackout** — 5/7-day window (wider in season), allows post-earnings reactions
- [x] **Cost modeling complete** — Brokerage, STT, exchange, SEBI, GST, stamp duty, slippage all included
- [x] **Breadth cached** — Computed once in morning scan, written to setup rows, read at pre-close
- [x] **Volume at 3 PM measured** — Real NSE U-shaped profile (85.1% at 3 PM, not clock-time 92%)
- [x] **Stale data excluded** — Symbols frozen >1 day behind dropped from both scan and RS cross-section
- [x] **Concentration limits enforced** — Max 8 concurrent, 3 per sector, slots allocated by score
- [x] **Backtest labeled as regression test** — Documentation clear on limitations (survivorship, non-overlapping trades)

---

## 📝 Documentation Fixes (TODAY)

- [x] Update `breakout/analysis/patterns.py` docstring
  - **Status:** DONE (2026-09-28)
  - **What:** Changed "Phase 1 ships three, Phase 2 will add" → "Nine fully implemented"
  - **Lines:** 1–11

---

## ⚠️ NOT YET IMPLEMENTED (Priority order)

### 🔴 HIGH PRIORITY (Easy wins)

#### 1. Job Failure Notifications (Item #12)
**Status:** NOT IMPLEMENTED  
**Why:** Three jobs daily (9:30, 3:20, EOD settle) — failures only caught by reading logs  
**What to do:** 
- Add Telegram alert on any `FAILED` in run_log table
- Query run_log in preclose_scan or eod_settle
- Send: "❌ morning_scan FAILED 2026-09-28 09:30 — [error message]"
- Also: daily "did all three run?" summary  
**Effort:** 30 minutes  
**File to modify:** `breakout/jobs/preclose_scan.py` (append ~20 lines at main())

---

### 🟡 MEDIUM PRIORITY (Signal tuning)

#### 2. Multi-Window RS (Item #5.2)
**Status:** DOCUMENTED LIMITATION (not implemented)  
**Why:** Single 63-day window may miss regime changes  
**What to do:**
- Implement 63d + 126d + 252d RS ranks with recent bias weighting
- Example: `rs_63 * 0.5 + rs_126 * 0.3 + rs_252 * 0.2`
- **Don't do this now** — wait for 4–6 weeks of paper trading data to justify the change
- Data already being logged to `alert_features` table  
**Effort:** 2–3 hours (when data available)  
**Files to modify:** `breakout/analysis/rs.py`, `breakout/jobs/morning_scan.py`

---

### 🟢 LOW PRIORITY (Infrastructure)

#### 3. Move data_cache Out of OneDrive (Item #14)
**Status:** NOT URGENT  
**Why:** OneDrive sync can lock the cache mid-run  
**What to do:**
- Move `data_cache/` to a local folder outside OneDrive
- Update config.yaml `paths.data_cache` to new location
- Benefits: No sync locks, faster I/O  
**Effort:** 1 hour  
**Files to modify:** `config.yaml`, possibly CI/deploy scripts

---

## 🧪 Validation Required Before Live Trading

- [ ] **4-week paper trading** with full logging
  - Check `alert_features` table weekly
  - Monitor: win rate, profit factor, avg win/loss R
  - Update `min_score_to_alert` if needed (currently 60)

- [ ] **Weekly backtest regression check**
  ```bash
  python -m breakout.backtest --days 60
  # Verify: win rate stable, no new pattern errors
  ```

- [ ] **Manual earnings verification** for high-score alerts (>70)
  - Double-check earnings dates in yfinance for symbols scoring >70
  - If source seems unreliable, maintain a blocklist

- [ ] **Sector concentration monitor**
  - First week: check if any sector hits the 3-position limit
  - Verify high-scoring candidates get slots (not first-come-first-served)

---

## 📊 Known Limitations (Won't Fix, Documented)

These are not bugs — they're architectural limitations documented in code:

1. **Backtest survivorship bias** — Backtest replays today's NIFTY 500 over history
   - All delisted/downgraded stocks absent from sample
   - Inflates win rate — use forward paper log for real edge estimate

2. **Non-overlapping trades in backtest** — Signals fired during held trades are discarded
   - Simplifies simulation, slightly optimistic

3. **No RS/sector scoring in backtest** — Uses 0/'flat' values
   - Makes live scores higher than backtest scores
   - A setup clearing `min_score_to_alert` live may not clear it in backtest

4. **No concentration limits in backtest** — Takes every signal
   - Live scanner would reject some due to sector/position caps
   - Another reason backtest numbers > live numbers

5. **Single-window RS** — 63-day lookback only
   - Documented in `rs.py` lines 22–27
   - Will address when data accumulates

6. **Earnings data completeness** — yfinance occasionally missing
   - Default-safe (missing = no blackout)
   - Manually verify for high-conviction setups

---

## 🚀 Ready for Live Trading?

**Technically:** ✅ YES  
**Recommended:** Run paper trading for 4–6 weeks first  
**Why:** See only the trades the system actually takes; backtest numbers are inflated

---

## 📞 Questions to Ask Before Going Live

1. **Capital allocation:** How much capital to risk per trade? (Currently configured 1%)
2. **Risk tolerance:** Max acceptable drawdown? (Currently circuit breakers at 3% daily, 10% monthly)
3. **Data source:** Using yfinance or Angel One API? (Currently yfinance)
4. **Schedule:** Where will jobs run? (Laptop, VPS, cloud?)
5. **Notifications:** Phone alerts on breakouts? (Telegram configured, email optional)

---

## 📚 Reading Order

1. **README.md** — What the system does, high-level strategy
2. **SYSTEM_AUDIT_2026_09_28.md** — Deep audit of each component (this folder)
3. **config.yaml** — Your specific thresholds and settings
4. **GUIDE.md** — How to run the jobs, interpret results, troubleshoot
5. **IMPROVEMENTS.md** — What was fixed in the 2026-09-12 review

---

**Last audit:** 2026-09-28  
**Patterns docstring updated:** 2026-09-28  
**Next action:** Implement job failure notifications (Item #12)
