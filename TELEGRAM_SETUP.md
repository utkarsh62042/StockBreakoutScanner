# Telegram Notification Setup

This guide explains how to set up Telegram notifications for the breakout scanner using two separate bots.

## Overview

The system sends notifications using **two separate bots**:

1. **Breakout Alerts Bot**: Sends fresh breakout signals when they are confirmed and entered
2. **Trades Summary Bot**: Sends daily summary of active trades and historic stats

Each bot sends to your personal Telegram chat (one-sided messages), keeping notifications organized by purpose.

---

## Prerequisites

1. A Telegram account
2. Two Telegram bots created with @BotFather:
   - One for breakout alerts
   - One for trades summary
3. You've sent `/start` to both bots

---

## Step 1: Create Your Two Telegram Bots

Create two separate bots via @BotFather:

1. **Bot 1** (for breakout alerts): Name it something like "BreakoutBot" and save the token
2. **Bot 2** (for trades summary): Name it something like "TradesSummaryBot" and save the token

You'll need both tokens in your `.env` file.

---

## Step 2: Get Your Telegram User ID

This is the chat ID for 1-on-1 messages with the bot.

### Method A: Using the Bot (Recommended)

1. Send `/start` or any message to your bot
2. Visit this URL in your browser (replace `BOT_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botBOT_TOKEN/getUpdates
   ```
3. Look for the JSON response. Find entries like:
   ```json
   {
     "message": {
       "from": {
         "id": 123456789,
         "first_name": "Your Name",
         ...
       },
       ...
     }
   }
   ```
4. The `"id"` value under `"from"` is your **User ID** (a positive number, not negative)

### Method B: Using @userinfobot

1. Open Telegram and search for `@userinfobot`
2. Send it any message
3. It will reply with your User ID

---

## Step 3: Configure Your `.env` File

Add/update these lines in your `.env` file:

```bash
# Bot 1 - Breakout Alerts
TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=123456789

# Bot 2 - Trades Summary
TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=654321:XYZ-GHI5678jklmn-opqr90Z3w4v567ax22
TELEGRAM_TRADES_SUMMARY_CHAT_ID=123456789
```

Where:
- `TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN`: Bot 1 token from @BotFather
- `TELEGRAM_BREAKOUT_ALERTS_CHAT_ID`: Your User ID (where Bot 1 sends alerts)
- `TELEGRAM_TRADES_SUMMARY_BOT_TOKEN`: Bot 2 token from @BotFather
- `TELEGRAM_TRADES_SUMMARY_CHAT_ID`: Your User ID (where Bot 2 sends summary)

**Note**: Both chat IDs are typically the same (your User ID), but you can use different IDs if you want messages from each bot to go to different destinations.

---

## Step 4: Enable Telegram in Config

Update your `config.yaml`:

```yaml
output:
  csv: true
  telegram: true
  email: false
```

---

## How It Works

### Breakout Alerts (Bot 1)

When a breakout is confirmed at 3:20 PM IST, Bot 1 sends you a message like:

```
🚨 BREAKOUT — INFY
Pattern: cup_and_handle | Score: 85
Entry: ₹1500.00  Stop: ₹1480.00
T1: ₹1530.00  T2: ₹1560.00
Shares: 10  Vol: 2.5x
```

One message per alert from the breakout bot.

### Trades Summary (Bot 2)

After end-of-day settlement (4:00 PM IST), Bot 2 sends you a summary message:

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

---

## Verification

To test your setup:

1. Run the preclose scan (or wait for next confirmed breakout)
2. Check if you receive the breakout alert message
3. Run the eod_settle job (or wait for 4:00 PM)
4. Check if you receive the trades summary message

---

## Troubleshooting

### "Telegram disabled: requests unavailable"
Install the requests library:
```bash
pip install requests
```

### I'm not receiving messages

**Check 1: User ID is correct**
- Visit `https://api.telegram.org/botTOKEN/getUpdates` with your bot token
- Send a test message to the bot
- Look for your User ID in the response (under `"from"` → `"id"`)

**Check 2: Bot can see you**
- Make sure you've sent `/start` to the bot first
- Try sending it another message

**Check 3: Config is correct**
- For breakout alerts: Verify `TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN` and `TELEGRAM_BREAKOUT_ALERTS_CHAT_ID` in `.env`
- For trades summary: Verify `TELEGRAM_TRADES_SUMMARY_BOT_TOKEN` and `TELEGRAM_TRADES_SUMMARY_CHAT_ID` in `.env`
- Verify `output.telegram: true` in `config.yaml`
- Chat IDs should be positive numbers (like `123456789`), not negative

**Check 4: `.env` is loaded**
- Make sure `.env` is in the project root directory
- Restart your application after changing `.env`

### Wrong bot receiving messages

Make sure:
- `TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN` is the correct token for the breakout bot
- `TELEGRAM_TRADES_SUMMARY_BOT_TOKEN` is the correct token for the summary bot
- Chat IDs match your User ID from Telegram

---

## Example `.env` File

```bash
# Bot 1 - Breakout Alerts (get token from @BotFather)
TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=123456789

# Bot 2 - Trades Summary (get token from @BotFather)
TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=654321:XYZ-GHI5678jklmn-opqr90Z3w4v567ax22
TELEGRAM_TRADES_SUMMARY_CHAT_ID=123456789

# Other credentials...
ANGELONE_API_KEY=your_key
```

---

## Advanced: Customization

The system now supports two separate bots by design. If you need different behavior:

**Same User ID for both bots**: Both bots send to your personal chat (recommended)
- Easier to manage, messages from both bots appear in the same conversation
- Just set both `*_CHAT_ID` values to your User ID

**Different destinations**: Each bot sends to a different chat
- Bot 1 sends to one User ID
- Bot 2 sends to a different User ID (or group)
- Useful if you want to separate notifications by severity or purpose

---

## Notes

- **Privacy**: Messages are sent only to your personal Telegram chat, not to groups
- **Frequency**: Alerts go out immediately when confirmed. Summary goes out once daily after settle
- **Fallback**: If a message fails to send, it's logged but doesn't stop the scan or settle job
- **Offline**: If you're offline, Telegram stores the message and delivers it when you're back online

---

## Chat IDs Reference

Save your settings:

| Setting | Value | Purpose |
|---------|-------|---------|
| Breakout Bot Token | `TELEGRAM_BREAKOUT_ALERTS_BOT_TOKEN=` | Sends breakout alerts |
| Breakout Chat ID | `TELEGRAM_BREAKOUT_ALERTS_CHAT_ID=` | Your User ID |
| Summary Bot Token | `TELEGRAM_TRADES_SUMMARY_BOT_TOKEN=` | Sends daily trades summary |
| Summary Chat ID | `TELEGRAM_TRADES_SUMMARY_CHAT_ID=` | Your User ID |
