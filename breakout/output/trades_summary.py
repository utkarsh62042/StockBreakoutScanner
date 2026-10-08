"""Send active trades and performance stats summary to Telegram.

Computes metrics on active and historic trades, formats them for a chat message,
and sends via the trades summary bot.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


def compute_active_trades(all_trades: list[dict]) -> list[dict]:
    """Filter and return only open/active trades (those without final pnl_r).

    Returns a list of open trades with relevant fields for display:
    symbol, pattern, entry_price, stop_loss, target_1, target_2, entry_date,
    days_in_trade, max_favorable, max_adverse.
    """
    active = [
        t for t in all_trades
        if t.get("state") and t["state"] not in ("STOPPED_OUT", "TARGET_HIT", "TARGET_1_HIT", "TIME_EXIT", "CANCELED")
    ]
    return active


def compute_historic_stats(all_trades: list[dict]) -> dict[str, Any]:
    """Compute performance statistics from all trades (settled only).

    Returns a dict with:
    - n: total settled trades
    - wins: number of winning trades
    - losses: number of losing trades
    - win_rate: win percentage
    - avg_r: average R-multiple
    - profit_factor: gross winners / gross losers (or None)
    - avg_days_held: average holding period
    - net_inr: net P&L in rupees
    """
    def _num(v) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None  # drop NaN

    # Only settled trades (those with a realized P&L)
    settled = [t for t in all_trades if _num(t.get("pnl_r")) is not None]

    if not settled:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "profit_factor": None,
            "avg_days_held": 0.0,
            "net_inr": 0.0,
        }

    # Win/loss analysis
    rs = [_num(t.get("pnl_r")) for t in settled]
    rs = [r for r in rs if r is not None]
    wins = sum(1 for r in rs if r > 0)
    losses = len(rs) - wins
    win_rate = (wins / len(rs) * 100.0) if rs else 0.0
    avg_r = (sum(rs) / len(rs)) if rs else 0.0

    # P&L analysis
    inrs = [_num(t.get("pnl_inr")) for t in settled]
    inrs = [v for v in inrs if v is not None]
    gross_profit = sum(v for v in inrs if v > 0)
    gross_loss = -sum(v for v in inrs if v < 0)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    net_inr = sum(inrs)

    # Days held
    days = [_num(t.get("days_held")) for t in settled]
    days = [d for d in days if d is not None]
    avg_days_held = (sum(days) / len(days)) if days else 0.0

    return {
        "n": len(settled),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_r": avg_r,
        "profit_factor": profit_factor,
        "avg_days_held": avg_days_held,
        "net_inr": net_inr,
    }


def send_trades_summary(
    bot_token: str,
    chat_id: str,
    active_trades: list[dict],
    stats: dict[str, Any],
) -> bool:
    """Format and send trades summary to Telegram chat.

    Args:
        bot_token: Telegram bot token for the trades summary bot
        chat_id: Telegram chat ID to send to
        active_trades: List of open trade dicts
        stats: Performance stats dict from compute_historic_stats

    Returns:
        True if send succeeded, False otherwise.
    """
    try:
        import requests
    except ImportError:
        logger.error("requests library not available for Telegram send")
        return False

    # Format active trades
    active_lines = []
    if active_trades:
        active_lines.append("📈 *Active Trades:*")
        for t in active_trades[:10]:  # Limit to 10 to avoid message size issues
            symbol = t.get("symbol", "?")
            entry = _fmt_price(t.get("entry_price"))
            days = t.get("days_in_trade", "?")
            mfav = _fmt_price(t.get("max_favorable"))
            madv = _fmt_price(t.get("max_adverse"))
            active_lines.append(
                f"{symbol}: entry {entry}, {days}d held, fav {mfav}, adv {madv}"
            )
    else:
        active_lines.append("📈 *Active Trades:* None")

    # Format stats
    pf = stats.get("profit_factor")
    pf_str = "∞" if pf == float("inf") else f"{pf:.2f}" if pf else "—"

    stats_lines = [
        "📊 *Performance (Settled):*",
        f"  Trades: {stats.get('n', 0)}",
        f"  Win Rate: {stats.get('win_rate', 0):.1f}% ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)",
        f"  Avg R: {stats.get('avg_r', 0):+.2f}",
        f"  Profit Factor: {pf_str}",
        f"  Avg Days Held: {stats.get('avg_days_held', 0):.1f}",
        f"  Net P&L: ₹{stats.get('net_inr', 0):+,.0f}",
    ]

    message = "\n".join(active_lines + [""] + stats_lines)

    # Send via Telegram Bot API
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("trades summary sent to Telegram")
            return True
        else:
            logger.warning(f"Telegram send failed: {response.status_code} {response.text}")
            return False
    except Exception as e:
        logger.exception(f"failed to send trades summary to Telegram: {e}")
        return False


def _fmt_price(v) -> str:
    """Format a price value for display, handling None/NaN."""
    if v is None:
        return "—"
    try:
        f = float(v)
        if f != f:  # NaN check
            return "—"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return "—"
