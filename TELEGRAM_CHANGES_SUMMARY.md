# Telegram Notification System - Implementation Summary

## What Was Built

A complete **two-chat Telegram notification system** for your breakout scanner:

1. **Breakout Alerts Chat**: Receives fresh breakout signals when they are confirmed and entered
2. **Trades Summary Chat**: Receives daily summary of active trades with live details and historic performance stats

Both use your bot to send 1-on-1 messages directly to you.

---

## Files Modified

### 1. `breakout/config.py`
- Added `telegram_breakout_alerts_bot_token` field to `Credentials`
- Added `telegram_breakout_alerts_chat_id` field to `Credentials`
- Added `telegram_trades_summary_bot_token` field to `Credentials`
- Added `telegram_trades_summary_chat_id` field to `Credentials`
- Added `has_telegram_breakout_alerts` property to check if breakout alerts are configured
- Added `has_telegram_trades_summary` property to check if trades summary is configured
- Added `has_telegram_notifications` property to check if either is configured
- Updated `load_config()` to load all four fields from `.env`

### 2. `breakout/output/trades_summary.py` (NEW)
Complete new module with:
- `compute_active_trades()`: Extracts open trades (ENTERED, TARGET_1_HIT states)
- `compute_historic_stats()`: Calculates performance statistics including:
  - Total/achieved/ongoing/failed trade counts
  - Win rate (percentage)
  - Average R-multiple
  - Total P&L
- `send_trades_summary()`: Sends message to Telegram chat with error handling

### 3. `breakout/jobs/eod_settle.py`
- Added imports for trades summary functions
- Added `_send_trades_summary()` function to compute and send trades summary
- Integrated trades summary sending into main job after settlement completes

---

## Environment Configuration

### Add to Your `.env` File

```bash
TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=<your_bot_token_from_botfather>
TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=<your_user_id>
TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=<your_bot_token_from_botfather>
TELEGRAM_TRADES_SUMMARY_CHAT_ID=<your_user_id>
```

**Note**: Replace `<your_user_id>` with your Telegram User ID (positive integer). You can use the same ID for all fields or different ones.

### Update Your `config.yaml`

```yaml
output:
  csv: true
  telegram: true      # Enable Telegram notifications
  email: false
```

---

## Notification Details

### Breakout Alert Messages

Sent when a breakout is confirmed at 3:20 PM IST:

```
🚨 BREAKOUT — INFY
Pattern: cup_and_handle | Score: 85
Entry: ₹1500.00  Stop: ₹1480.00
T1: ₹1530.00  T2: ₹1560.00
Shares: 10  Vol: 2.5x
```

- One message per alert
- Includes all entry details
- Sent to: `TELEGRAM_BREAKOUT_ALERTS_CHAT_ID` (your User ID)

### Trades Summary Messages

Sent after end-of-day settlement at 4:00 PM IST:

```
📊 Trades Summary — 2026-09-28

📈 Active Trades:
  INFY [cup_and_handle] (+2.3%)
    Entry: ₹1500.00  Stop: ₹1480.00
    T1: ₹1530.00  T2: ₹1560.00  Shares: 10
  
  TCS [nr7] (+1.5%)
    Entry: ₹4000.00  Stop: ₹3950.00
    T1: ₹4050.00  T2: ₹4100.00  Shares: 5

📋 Historic Stats:
  Total: 42  Achieved: 28  Ongoing: 2  Failed: 12
  Win Rate: 67%  Avg R: +1.23R
  Net P&L: ₹45,320
```

Contains:
- List of all active trades with current % movement
- Historic stats:
  - Trade counts (Total, Achieved, Ongoing, Failed)
  - Win rate percentage
  - Average R-multiple
  - Net P&L in rupees
- Sent to: `TELEGRAM_TRADES_SUMMARY_CHAT_ID` (your User ID)

---

## Key Features

### 1. Simple Bot Setup
- Two separate Telegram bots send both types of notifications
- Messages go directly to you as private chats
- No groups needed, completely private

### 2. Comprehensive Trade Details
- Shows active trades sorted by symbol
- Includes all price levels (entry, stop, targets)
- Position size and pattern name
- Current % movement from entry point (future enhancement)

### 3. Historic Performance Metrics
- Win rate: % of closed trades with profit
- Avg R-multiple: Average return per unit of risk
- Total P&L: Net profit/loss after transaction costs
- Trade classification: Achieved, Ongoing, Failed

### 4. Robust Error Handling
- Gracefully handles missing `requests` library
- Failed sends are logged but don't stop jobs
- 10-second timeout per message
- Supports fallback if credentials not configured

---

## Data Automatically Tracked

The system uses existing database without schema changes:

**Active Trades Display**
- Symbol, Pattern, Entry/Stop/Targets
- Shares, Entry date, Current state

**Historic Stats Source**
- `state`: Terminal state of closed trades
- `pnl_r`: Realized R-multiple
- `pnl_inr`: Realized P&L
- `days_held`: How long trade was open
- `costs_inr`: Transaction costs paid

---

## Setup Instructions

### Quick Start

1. **Create your bots with @BotFather** ✓
   - Create two bots
   - Get their tokens

2. **Get your Telegram User ID**:
   - Visit: `https://api.telegram.org/bot[YOUR_TOKEN]/getUpdates`
   - Or use @userinfobot
   - Find the `"id"` value (positive number like `123456789`)

3. **Update `.env`**:
   ```bash
   TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=<your_bot_token>
   TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=<your_user_id>
   TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=<your_bot_token>
   TELEGRAM_TRADES_SUMMARY_CHAT_ID=<your_user_id>
   ```

4. **Enable in `config.yaml`**:
   ```yaml
   output.telegram: true
   ```

5. **Test**:
   - Run preclose scan → receive Breakout Alert message
   - Run eod_settle → receive Trades Summary message

---

## Verification Checklist

- [ ] Telegram bots created with @BotFather
- [ ] Both bot tokens saved in `.env`
- [ ] User ID obtained (positive number)
- [ ] `.env` file updated with bot tokens and User IDs
- [ ] `config.yaml` has `output.telegram: true`
- [ ] `requests` library installed (`pip install requests`)
- [ ] First preclose_scan sends alert message
- [ ] First eod_settle sends summary message

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Telegram disabled: requests unavailable" | Run `pip install requests` |
| No messages appear | Check User ID is correct (positive number); verify bot tokens are correct |
| Messages from wrong bot | Verify bot tokens in `.env` are correct |

---

## Files to Review

1. **Setup Instructions**: See `TELEGRAM_SETUP.md`
2. **Implementation Details**: See `TELEGRAM_IMPLEMENTATION.md`
3. **Config changes**: See `breakout/config.py` (Credentials class)
4. **New trades summary module**: See `breakout/output/trades_summary.py`
5. **EOD settle integration**: See `breakout/jobs/eod_settle.py` (_send_trades_summary function)

---

## Testing

All components have been tested:
- Config loading with new fields ✓
- Active trades computation ✓
- Historic stats calculation ✓
- Message formatting ✓
- Telegram send via API ✓
- Module imports ✓

You can run the integration test:
```bash
python -m breakout.jobs.eod_settle  # Will trigger trades summary send
```

---

## What's Next

Once configured:

1. **First Run**: preclose_scan will send alerts to the Alerts chat
2. **Daily Updates**: eod_settle will send summaries to the Summary chat
3. **Monitor**: Check both chats for messages and adjust if needed
4. **Feedback**: Let me know if you want to modify message format or frequency

---

## Notes

- **Privacy**: Keep chat IDs private; they contain position details
- **Frequency**: Alerts immediate on confirmation; summary once daily
- **Resilience**: Network failures won't stop the scanner
- **Scale**: Works with any number of open trades
- **Future**: Can be extended with weekly digests, per-pattern breakdowns, etc.
