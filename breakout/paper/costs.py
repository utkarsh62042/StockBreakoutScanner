"""Round-trip transaction costs for an NSE delivery (CNC) trade.

Nothing in the paper log or the backtest modelled costs before this, which made
every R-multiple optimistic by a constant amount — and on the 3-5% moves this
strategy targets, "a constant amount" is 5-10% of gross P&L. Marginal trades
that read as small winners are net losers once costs are in.

The charges, per the NSE/SEBI schedule for equity delivery (both legs unless
noted):

    brokerage      broker-specific; Angel One charges the lower of 0.1% and
                   ₹20 per executed order
    STT            0.1% of turnover, both legs
    exchange txn   0.00297% of turnover (NSE)
    SEBI turnover  0.0001% of turnover
    stamp duty     0.015% of turnover, BUY leg only
    GST            18% on (brokerage + exchange txn + SEBI fees)

`slippage_pct` is not a statutory charge but belongs in the same place: the
fills the tracker assumes (exactly at the stop, exactly at the target) are
idealised, so a per-leg haircut keeps the estimate honest. It defaults to 0.05%
per leg.

All rates live in `config.yaml` under `costs:` so the whole model can be turned
off (`enabled: false`) to compare gross against net.
"""

from __future__ import annotations

from breakout.config import CostsConfig


def _leg_cost(turnover: float, cfg: CostsConfig, *, is_buy: bool) -> float:
    """Statutory + brokerage + slippage cost for one leg, in INR."""
    if turnover <= 0:
        return 0.0
    brokerage = min(
        turnover * cfg.brokerage_pct / 100.0, cfg.brokerage_max_inr
    )
    exchange = turnover * cfg.exchange_txn_pct / 100.0
    sebi = turnover * cfg.sebi_turnover_pct / 100.0
    stt = turnover * cfg.stt_pct / 100.0
    stamp = turnover * cfg.stamp_duty_pct / 100.0 if is_buy else 0.0
    gst = (brokerage + exchange + sebi) * cfg.gst_pct / 100.0
    slippage = turnover * cfg.slippage_pct / 100.0
    return brokerage + exchange + sebi + stt + stamp + gst + slippage


def round_trip_cost(
    entry_price: float,
    exit_price: float,
    shares: float,
    cfg: CostsConfig,
) -> float:
    """Total INR cost of buying `shares` at `entry_price` and selling at
    `exit_price`. Returns 0.0 when costs are disabled or the position is empty.
    """
    if not cfg.enabled or shares <= 0:
        return 0.0
    return (
        _leg_cost(entry_price * shares, cfg, is_buy=True)
        + _leg_cost(exit_price * shares, cfg, is_buy=False)
    )


def cost_per_share(entry_price: float, exit_price: float, cfg: CostsConfig) -> float:
    """Round-trip cost attributable to a single share, in INR.

    Used to net down `pnl_r`, which is a per-share quantity. Costs are
    proportional to turnover except for the brokerage cap, so this is computed
    on a one-share notional: at ₹5-15k position sizes the percentage brokerage
    is well under the ₹20 cap anyway, and the cap only ever makes the per-share
    figure an overestimate — the conservative direction.
    """
    if not cfg.enabled:
        return 0.0
    return round_trip_cost(entry_price, exit_price, 1.0, cfg)


def settle_pnl(
    entry_price: float,
    exit_price: float,
    shares: float,
    stop_loss: float,
    cfg: CostsConfig,
) -> tuple[float, float, float, float]:
    """Return `(gross_inr, costs_inr, net_inr, net_r)` for a closed trade.

    `net_r` nets the per-share cost off the per-share move before dividing by
    risk per share, so R-multiples and rupees tell the same story.
    """
    gross_inr = (exit_price - entry_price) * shares
    costs_inr = round_trip_cost(entry_price, exit_price, shares, cfg)
    risk_per_share = max(1e-9, abs(entry_price - stop_loss))
    net_move = (exit_price - entry_price) - cost_per_share(entry_price, exit_price, cfg)
    return gross_inr, costs_inr, gross_inr - costs_inr, net_move / risk_per_share
