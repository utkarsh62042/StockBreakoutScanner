# Telegram Notification Implementation

This document describes the implementation of the two-group Telegram notification system.

## Architecture

### Components

1. **Config Updates** (`breakout/config.py`)
   - Added two new credential fields:
     - `telegram_breakout_alerts_chat_id`: Chat ID for fresh breakout alerts
     - `telegram_trades_summary_chat_id`: Chat ID for daily trades summary
   - New property `has_telegram_notifications` to check if both chat IDs are configured

2. **Trades Summary Module** (`breakout/output/trades_summary.py`)
   - `TradeDetail`: Dataclass for active trade information
   - `HistoricStats`: Dataclass for performance statistics
   - `compute_active_trades()`: Extracts currently open trades
   - `compute_historic_stats()`: Calculates win rate, avg R, P&L
   - `format_telegram_trades_summary()`: Formats message for Telegram
   - `send_trades_summary()`: Sends message to Telegram chat

3. **Alert Dispatcher Updates** (`breakout/output/alerts.py`)
   - Enhanced `TelegramChannel` to accept `alerts_chat_id` parameter
   - Routes alerts to dedicated chat instead of default chat

4. **EOD Settle Job Integration** (`breakout/jobs/eod_settle.py`)
   - Calls `_send_trades_summary()` after settlement completes
   - Sends active trades + historic stats to Trades Summary chat

## Data Flow

### Breakout Alerts

```
preclose_scan → detect confirmation → create Alert
                                      ↓
                                  dispatch_alerts()
                                      ↓
                              TelegramChannel.emit()
                                      ↓
                         TELEGRAM_BREAKOUT_ALERTS_CHAT_ID
```

### Trades Summary

```
eod_settle → settle all trades → _send_trades_summary()
                                     ↓
                    compute_active_trades()
                    compute_historic_stats()
                                     ↓
                    format_telegram_trades_summary()
                                     ↓
                           send_trades_summary()
                                     ↓
                      TELEGRAM_TRADES_SUMMARY_CHAT_ID
```

## Configuration

### Environment Variables (`.env`)

```bash
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<user_id>
TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=<user_id>
TELEGRAM_TRADES_SUMMARY_CHAT_ID=<user_id>
```

Note: All chat IDs should be positive integers (your Telegram User ID). You can use the same User ID for both notifications or different ones.

### Config File (`config.yaml`)

```yaml
output:
  csv: true
  telegram: true
  email: false
```

## Features

### Active Trades Extraction

Identifies trades in open states:
- `ALERTED` (legacy)
- `ENTERED` (actively trading)
- `TARGET_1_HIT` (partial profit taken, still holding)

### Historic Stats Calculation

Computes from all closed trades:
- **Total trades**: All trades ever created
- **Achieved trades**: Closed with profit (TARGET_HIT or TARGET_1_HIT)
- **Failed trades**: Closed with loss (STOPPED_OUT)
- **Ongoing trades**: Still open
- **Win rate**: % of closed trades with profit
- **Avg R**: Average R-multiple across all closed trades
- **Net P&L**: Total P&L after transaction costs

### Auto-Reset on Wipe

The system detects when the paper trades sheet is wiped:
1. Computes hash of all trade IDs at runtime
2. If hash changes significantly, the sheet was wiped
3. Historic stats automatically reset for next period

## Telegram Message Format

### Alert Message
```
🚨 BREAKOUT — INFY
Pattern: cup_and_handle | Score: 85
Entry: ₹1500.00  Stop: ₹1480.00
T1: ₹1530.00  T2: ₹1560.00
Shares: 10  Vol: 2.5x
```

One message per alert for easy scanning.

### Summary Message
```
📊 Trades Summary — 2026-09-28

📈 Active Trades:
  INFY [cup_and_handle] (+2.3%)
    Entry: ₹1500.00  Stop: ₹1480.00
    T1: ₹1530.00  T2: ₹1560.00  Shares: 10

📋 Historic Stats:
  Total: 42  Achieved: 28  Ongoing: 2  Failed: 12
  Win Rate: 67%  Avg R: +1.23R
  Net P&L: ₹45,320
```

## Error Handling

- If `requests` library is unavailable, Telegram is disabled with a warning
- Send failures are logged but don't stop the job
- Messages are sent with a 10-second timeout
- Both group notifications are optional; missing configuration doesn't crash the system

## Trade State Machine

The system tracks these states:

```
ENTERED ────────→ TARGET_1_HIT ──→ TARGET_HIT (final)
   ↓                   ↓          ↓
   └─→ STOPPED_OUT ────┘          (final)
   ↓
   └─→ TIME_EXIT ────────────────→ (final)
```

For active trades notification, only ENTERED and TARGET_1_HIT are shown.

## Database Schema Integration

No schema changes required. Uses existing `paper_trades` table:
- `state`: Current trade state
- `symbol`: Stock symbol
- `pattern`: Pattern name
- `entry_price`, `stop_loss`, `target_1`, `target_2`: Price levels
- `shares`: Position size
- `entry_date`: When trade was entered
- `pnl_r`: Realised R-multiple (closed trades only)
- `pnl_inr`: Realised P&L in rupees (closed trades only)

## Testing

Test with sample data:
```python
from breakout.output.trades_summary import (
    compute_active_trades,
    compute_historic_stats,
    format_telegram_trades_summary
)

trades = [...]  # list of trade dicts
active = compute_active_trades(trades)
stats = compute_historic_stats(trades)
msg = format_telegram_trades_summary(active, stats)
```

## Deployment

1. Update `.env` with bot token and group IDs
2. Set `output.telegram: true` in `config.yaml`
3. Verify bot is admin in both groups
4. Next preclose_scan sends alerts to Breakout Alerts group
5. Next eod_settle sends summary to Trades Summary group

## Performance

- Trade summary computation: O(n) where n = number of trades
- Hash computation: O(n) for trade ID list
- Telegram send: Network bound, 10-second timeout per message
- Memory: Minimal, no caching beyond what the Store provides

## Future Enhancements

Potential additions:
- Scheduled summaries (e.g., weekly digest)
- Detailed trade-by-trade breakdowns
- Performance charts as images
- Real-time position updates (requires market data integration)
- Multiple portfolio support
- Webhook-based triggers for specific conditions

