# Telegram Notification Implementation

This document describes the implementation of the two-bot Telegram notification system.

## Architecture

### Components

1. **Config Updates** (`breakout/config.py`)
   - Added four new credential fields:
     - `telegram_breakout_alerts_bot_token`: Bot token for breakout alerts
     - `telegram_breakout_alerts_chat_id`: Chat ID for fresh breakout alerts
     - `telegram_trades_summary_bot_token`: Bot token for trades summary
     - `telegram_trades_summary_chat_id`: Chat ID for daily trades summary
   - New properties:
     - `has_telegram_breakout_alerts`: Check if breakout alerts are configured
     - `has_telegram_trades_summary`: Check if trades summary is configured
     - `has_telegram_notifications`: Check if either is configured

2. **Trades Summary Module** (`breakout/output/trades_summary.py`)
   - `compute_active_trades()`: Extracts currently open trades
   - `compute_historic_stats()`: Calculates win rate, avg R, P&L
   - `send_trades_summary()`: Sends message to Telegram chat

3. **EOD Settle Job Integration** (`breakout/jobs/eod_settle.py`)
   - Calls `_send_trades_summary()` after settlement completes
   - Sends active trades + historic stats to Trades Summary bot

## Data Flow

### Trades Summary

```
eod_settle → settle all trades → _send_trades_summary()
                                     ↓
                    compute_active_trades()
                    compute_historic_stats()
                                     ↓
                           send_trades_summary()
                                     ↓
                      TELEGRAM_TRADES_SUMMARY_BOT_TOKEN
                      TELEGRAM_TRADES_SUMMARY_CHAT_ID
```

## Configuration

### Environment Variables (`.env`)

```bash
TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=<bot_token>
TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=<user_id>
TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=<bot_token>
TELEGRAM_TRADES_SUMMARY_CHAT_ID=<user_id>
```

Note: All chat IDs should be positive integers (your Telegram User ID).

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
- `ENTERED` (actively trading)
- `TARGET_1_HIT` (partial profit taken, still holding)

### Historic Stats Calculation

Computes from all closed trades:
- **Total trades**: All trades ever created
- **Achieved trades**: Closed with profit
- **Failed trades**: Closed with loss
- **Ongoing trades**: Still open
- **Win rate**: % of closed trades with profit
- **Avg R**: Average R-multiple across all closed trades
- **Net P&L**: Total P&L after transaction costs

## Telegram Message Format

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
- Telegram configuration is optional; missing credentials don't crash the system

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
    send_trades_summary
)

trades = [...]  # list of trade dicts
active = compute_active_trades(trades)
stats = compute_historic_stats(trades)
success = send_trades_summary(bot_token, chat_id, active, stats)
```

## Deployment

1. Update `.env` with bot tokens and chat IDs
2. Set `output.telegram: true` in `config.yaml`
3. Next eod_settle sends summary to Trades Summary bot
4. Monitor logs for successful sends

## Performance

- Trade summary computation: O(n) where n = number of trades
- Telegram send: Network bound, 10-second timeout per message
- Memory: Minimal, no caching beyond what the Store provides

## Future Enhancements

Potential additions:
- Scheduled summaries (e.g., weekly digest)
- Detailed trade-by-trade breakdowns
- Performance charts as images
- Real-time position updates
- Multiple portfolio support
- Webhook-based triggers for specific conditions
