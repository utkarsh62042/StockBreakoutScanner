# Telegram Notification Setup

This guide explains how to set up the two Telegram notification groups for the breakout scanner.

## Overview

The system sends notifications to **two separate Telegram groups**:

1. **Breakout Alerts Group**: Fresh breakout signals when they are entered
2. **Trades Summary Group**: Daily summary of active trades and historic stats

Both notifications are sent to dedicated groups so you can mute one without missing the other.

---

## Prerequisites

1. A Telegram bot created with BotFather
2. Two Telegram groups (or channels) created
3. The bot added as an admin to both groups
4. Your `.env` file configured with bot credentials

---

## Step 1: Create/Get Your Telegram Bot

If you don't have a bot yet:

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts to create a bot
3. Copy the **Bot Token** (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

---

## Step 2: Create Two Telegram Groups

### Group 1: Breakout Alerts
Create a group named something like:
- `My Breakout Alerts`
- `Trading Signals`
- `Entry Alerts`

Add your bot as an admin to this group.

### Group 2: Trades Summary
Create a group named something like:
- `My Trades Summary`
- `Active Positions`
- `Trading Dashboard`

Add your bot as an admin to this group.

---

## Step 3: Get the Group IDs

There are two ways to get the group IDs:

### Method A: Using the Bot (Recommended)

1. Send a message to your bot: `/start` or any text
2. Add your bot to the groups and send a test message in each group
3. Visit this URL in your browser (replace `BOT_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botBOT_TOKEN/getUpdates
   ```
4. Look for the JSON response. Find entries like:
   ```json
   {
     "message": {
       "chat": {
         "id": -1001234567890,
         ...
       }
     }
   }
   ```
5. The `"id"` value (with the minus sign) is your group ID

### Method B: Using @RawDataBot

1. Add [@RawDataBot](https://t.me/RawDataBot) to each group
2. Send a message in the group
3. @RawDataBot will show you the group ID
4. Remove @RawDataBot from the groups

---

## Step 4: Configure Your `.env` File

Add/update these lines in your `.env` file:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_BREAKOUT_ALERTS_GROUP_ID=-1001111111111
TELEGRAM_TRADES_SUMMARY_GROUP_ID=-1002222222222
```

Where:
- `TELEGRAM_BOT_TOKEN`: Your bot token from BotFather
- `TELEGRAM_CHAT_ID`: Legacy (can be same as TELEGRAM_BREAKOUT_ALERTS_GROUP_ID)
- `TELEGRAM_BREAKOUT_ALERTS_GROUP_ID`: Your breakout alerts group ID
- `TELEGRAM_TRADES_SUMMARY_GROUP_ID`: Your trades summary group ID

---

## Step 5: Enable Telegram in Config

Update your `config.yaml`:

```yaml
output:
  csv: true
  telegram: true
  email: false
```

---

## How It Works

### Breakout Alerts Group

When a breakout is confirmed at 3:20 PM IST, the system sends a message like:

```
🚨 BREAKOUT — INFY
Pattern: cup_and_handle | Score: 85
Entry: ₹1500.00  Stop: ₹1480.00
T1: ₹1530.00  T2: ₹1560.00
Shares: 10  Vol: 2.5x
```

One message per alert, so they're easy to scan on your phone.

### Trades Summary Group

After end-of-day settlement (4:00 PM IST), the system sends a summary:

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

**The stats automatically reset** when you wipe the paper trading sheet (the system detects the change and resets the tracking date).

---

## Verification

To test your setup:

1. Run the preclose scan (or create a test alert)
2. Check if the breakout alert appears in the Breakout Alerts group
3. Run the eod_settle job (or wait for 4:00 PM)
4. Check if the trades summary appears in the Trades Summary group

---

## Troubleshooting

### "Telegram disabled: requests unavailable"
Install the requests library:
```bash
pip install requests
```

### Group ID showing in the console but messages not sent
- Verify the bot is an admin in both groups
- Check that the group IDs in `.env` are correct (include the minus sign)
- Verify `output.telegram: true` in `config.yaml`

### Stats not resetting when you wipe the sheet
The system detects wipes by hashing the trade IDs. Make sure:
- You're using the native Excel "delete all rows" not just clearing values
- The wipe happens **before** the next eod_settle run

### Wrong group receiving the message
Double-check that:
- `TELEGRAM_BREAKOUT_ALERTS_GROUP_ID` points to the Alerts group
- `TELEGRAM_TRADES_SUMMARY_GROUP_ID` points to the Summary group

---

## Example `.env` File

```bash
# Telegram Bot Token (get from @BotFather)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11

# Legacy chat ID (can be same as alerts group)
TELEGRAM_CHAT_ID=-1001234567890

# Two dedicated groups
TELEGRAM_BREAKOUT_ALERTS_GROUP_ID=-1001234567890
TELEGRAM_TRADES_SUMMARY_GROUP_ID=-1009876543210

# Other credentials...
ANGELONE_API_KEY=your_key
```

---

## Notes

- **Privacy**: The trade summaries contain position details. Only add people you trust to these groups.
- **Frequency**: Alerts go out as they're confirmed. Summary goes out once daily after settle.
- **No sensitive data**: The bot token is the only credential the bot needs; it's already in your `.env` and not committed.
- **Fallback**: If a message fails to send, it's logged but doesn't stop the scan or settle job.

---

## Group IDs Reference

Save your group IDs somewhere safe:

| Group | ID |
|-------|-----|
| Breakout Alerts | `TELEGRAM_BREAKOUT_ALERTS_GROUP_ID=` |
| Trades Summary | `TELEGRAM_TRADES_SUMMARY_GROUP_ID=` |

