"""Price-series sanity checks.

`Store.upsert_prices` re-bases the cache whenever the feed's adjustment changes,
and the yfinance backend requests adjusted bars — but neither helps for the
corporate action that actually matters on NSE.

Splits and bonuses are handled by the feed (yfinance back-adjusts them whatever
`auto_adjust` says). **Demergers and capital reductions are not**: Yahoo has no
split record for them, so the price drop sits in the series as though it were a
day's trading. That is the common case here, not an exotic one — measured
against the live NIFTY 500 cache, VEDL (-65%), ABFRL (-67%), TMPV (-40%) and
TRENT (-33%) all carry one, and refetching does not clear them because the feed
never adjusted them in the first place.

It is the worst kind of bad data because it is silent and plausible-looking.
From that bar onward the 52-week high, every base detector, ATR (and so the
stop), the 30-week SMA slope (and so the stage) and the 63-day relative-strength
return are all measuring a discontinuity instead of price action. The scanner
would rather skip a symbol than rank it on a cliff, so the jobs treat a
discontinuity as a hard skip.
"""

from __future__ import annotations

import logging

import pandas as pd


logger = logging.getLogger(__name__)


# Bands for a plausible single-day close-to-close move on a NIFTY 500 name.
# Deliberately asymmetric: splits, bonuses and demergers only ever *divide* the
# price, so the downside is where corporate actions hide, while genuine one-day
# upside of 20-30% does happen on results or a takeover bid.
#
# These bands knowingly over-reject, and no threshold can do better. Measured
# against the live cache, the drops in this range are a mix of real actions
# (ABFRL -67%, VEDL -65%, TMPV -40%, TRENT -33% — all demergers, so the ratios
# are arbitrary rather than clean fractions) and real crashes (INDUSINDBK -27%
# on its 2025 disclosure, IEX -30% on market-coupling news). They overlap, so
# a rule that lets the crashes through would also let the demergers through.
#
# We accept the false positives because they are nearly free. A stock that just
# fell 27% in a day is not going to pass the Stage 2 gate for months anyway, so
# excluding it costs no real candidate; and the exclusion is temporary — the bar
# ages out of the 300-day analysis window on its own. Ranking a genuine
# discontinuity, by contrast, produces a confident alert built on a number that
# never happened.
MAX_PLAUSIBLE_DROP = 0.25   # -25% in a day
MAX_PLAUSIBLE_JUMP = 0.40   # +40% in a day


def find_price_discontinuity(
    df: pd.DataFrame,
    max_drop: float = MAX_PLAUSIBLE_DROP,
    max_jump: float = MAX_PLAUSIBLE_JUMP,
) -> tuple[str, float] | None:
    """Return `(date, move)` for the largest implausible one-day close move, or
    None if the series looks continuous.

    `move` is a fraction (-0.5 = a -50% day). Only the single worst offender is
    reported — one is enough to disqualify the series, and naming it makes the
    log line actionable.
    """
    if df is None or len(df) < 2 or "close" not in df.columns:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    moves = close.pct_change()
    bad = moves[(moves <= -abs(max_drop)) | (moves >= abs(max_jump))].dropna()
    if bad.empty:
        return None
    worst = bad.abs().idxmax()
    stamp = worst.date().isoformat() if hasattr(worst, "date") else str(worst)[:10]
    return stamp, float(moves.loc[worst])


def is_continuous(df: pd.DataFrame, symbol: str = "") -> bool:
    """`find_price_discontinuity` as a pass/fail gate, logging what it rejected."""
    found = find_price_discontinuity(df)
    if found is None:
        return True
    when, move = found
    logger.warning(
        f"{symbol or 'series'}: {move * 100:+.1f}% move on {when} — unadjusted "
        "corporate action, or a crash this strategy would not trade either way; "
        "skipping while that bar is in the window"
    )
    return False
