# Telegram Notification System - Implementation Summary

## What Was Built

A complete **two-group Telegram notification system** for your breakout scanner:

1. **Breakout Alerts Group**: Receives fresh breakout signals when they are confirmed and entered
2. **Trades Summary Group**: Receives daily summary of active trades with live details and historic performance stats

Both groups receive dedicated messages so you can manage notifications independently.

---

## Files Modified

### 1. `breakout/config.py`
- Added `telegram_breakout_alerts_group_id` field to `Credentials`
- Added `telegram_trades_summary_group_id` field to `Credentials`
- Added `has_telegram_groups` property to check if both groups are configured
- Updated `load_config()` to load both group IDs from `.env`

### 2. `breakout/output/alerts.py`
- Enhanced `TelegramChannel.__init__()` to accept `alerts_group_id` parameter
- Updated `TelegramChannel.emit()` to send alerts to the dedicated group
- Updated `build_channels()` to pass alerts group ID from config

### 3. `breakout/output/trades_summary.py` (NEW)
Complete new module with:
- `TradeDetail`: Dataclass for individual trade information
- `HistoricStats`: Dataclass for performance statistics
- `compute_active_trades()`: Extracts open trades (ENTERED, TARGET_1_HIT states)
- `compute_historic_stats()`: Calculates:
  - Total/achieved/ongoing/failed trade counts
  - Win rate (percentage)
  - Average R-multiple
  - Total P&L
  - Trade hash (for detecting sheet wipes)
- `format_telegram_trades_summary()`: Formats professional message for Telegram
- `send_trades_summary()`: Sends message to Telegram group with error handling

### 4. `breakout/jobs/eod_settle.py`
- Added imports for trades summary functions
- Added `_send_trades_summary()` function to compute and send trades summary
- Integrated trades summary sending into main job after settlement completes

---

## Environment Configuration

### Add to Your `.env` File

```bash
TELEGRAM_BOT_TOKEN=<your_bot_token_from_botfather>
TELEGRAM_CHAT_ID=<legacy_chat_id_or_alerts_group>
TELEGRAM_BREAKOUT_ALERTS_GROUP_ID=<group_id_with_minus_sign>
TELEGRAM_TRADES_SUMMARY_GROUP_ID=<group_id_with_minus_sign>
```

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
- Sent to: `TELEGRAM_BREAKOUT_ALERTS_GROUP_ID`

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
- Sent to: `TELEGRAM_TRADES_SUMMARY_GROUP_ID`

---

## Key Features

### 1. Two Separate Groups
- **Alerts Group**: Only breakout confirmations
- **Summary Group**: Only daily summaries
- Avoid notification fatigue by controlling each channel independently

### 2. Auto-Reset on Sheet Wipe
- System detects when paper trades sheet is wiped
- Automatically resets stats tracking for next period
- Detection based on trade ID hash changes

### 3. Comprehensive Trade Details
- Shows active trades sorted by symbol
- Includes all price levels (entry, stop, targets)
- Position size and pattern name
- Current % movement from entry point (future enhancement)

### 4. Historic Performance Metrics
- Win rate: % of closed trades with profit
- Avg R-multiple: Average return per unit of risk
- Total P&L: Net profit/loss after transaction costs
- Trade classification: Achieved, Ongoing, Failed

### 5. Robust Error Handling
- Gracefully handles missing `requests` library
- Failed sends are logged but don't stop jobs
- 10-second timeout per message
- Supports fallback if group IDs not configured

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

1. **Create Telegram bot** (if needed):
   - Chat with @BotFather
   - Get your bot token

2. **Create two groups**:
   - "My Breakout Alerts"
   - "My Trades Summary"
   - Add bot as admin to both

3. **Get group IDs**:
   - Visit: `https://api.telegram.org/botTOKEN/getUpdates`
   - Send a message in each group
   - Find the group ID (with minus sign)

4. **Update `.env`**:
   ```bash
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_BREAKOUT_ALERTS_GROUP_ID=<group_id>
   TELEGRAM_TRADES_SUMMARY_GROUP_ID=<group_id>
   ```

5. **Enable in `config.yaml`**:
   ```yaml
   output.telegram: true
   ```

6. **Test**:
   - Run preclose scan → check Alerts group
   - Run eod_settle → check Summary group

---

## Verification Checklist

- [ ] Telegram bot created with @BotFather
- [ ] Two groups created and bot added as admin
- [ ] Group IDs obtained (with minus sign)
- [ ] `.env` file updated with token and group IDs
- [ ] `config.yaml` has `output.telegram: true`
- [ ] `requests` library installed (`pip install requests`)
- [ ] First preclose_scan sends alert to Alerts group
- [ ] First eod_settle sends summary to Summary group
- [ ] Stats reset after wiping paper trades sheet

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Telegram disabled: requests unavailable" | Run `pip install requests` |
| No messages appear | Check bot is admin in groups; verify group IDs include minus sign |
| Wrong group gets message | Verify group IDs in `.env` are correctly assigned |
| Stats not resetting | Ensure you delete all rows (not just clear) when wiping sheet |

---

## Files to Review

1. **Setup Instructions**: See `TELEGRAM_SETUP.md`
2. **Implementation Details**: See `TELEGRAM_IMPLEMENTATION.md`
3. **Config changes**: See `breakout/config.py` (lines ~163-195)
4. **New trades summary module**: See `breakout/output/trades_summary.py`
5. **Alert dispatcher updates**: See `breakout/output/alerts.py` (lines ~123-161)
6. **EOD settle integration**: See `breakout/jobs/eod_settle.py`

---

## Testing

All components have been tested:
- Config loading with new fields ✓
- Active trades computation ✓
- Historic stats calculation ✓
- Message formatting ✓
- Alert object creation ✓
- Module imports ✓

You can run the integration test:
```bash
python breakout/jobs/eod_settle.py  # Will trigger trades summary send
```

---

## What's Next

Once configured:

1. **First Run**: preclose_scan will send alerts to the Alerts group
2. **Daily Updates**: eod_settle will send summaries to the Summary group
3. **Monitor**: Check both groups for messages and adjust if needed
4. **Feedback**: Let me know if you want to modify message format or frequency

---

## Notes

- **Privacy**: Keep group links private; they contain position details
- **Frequency**: Alerts immediate on confirmation; summary once daily
- **Resilience**: Network failures won't stop the scanner
- **Scale**: Works with any number of open trades
- **Future**: Can be extended with weekly digests, per-pattern breakdowns, etc.

