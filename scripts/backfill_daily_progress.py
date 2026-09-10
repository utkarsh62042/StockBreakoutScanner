r"""Backfill the `days_in_trade` / `daily_moves` columns on paper_trades.

    .\.venv\Scripts\python.exe scripts\backfill_daily_progress.py
    .\.venv\Scripts\python.exe scripts\backfill_daily_progress.py --dry-run

The settle job maintains both columns from now on; this script fills them in
for trades that were already entered before the columns existed, using the
OHLCV bars cached in the workbook's `prices` sheet. Closed trades are measured
up to their exit date, open trades up to the last cached bar. Trades with no
entry (still ALERTED, or CANCELED before entry) are left blank; a trade entered
today shows D0 with no moves yet.

Run it once after upgrading. It is idempotent — the strings are recomputed from
price history, never appended to, so re-running is harmless.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from breakout.config import load_config  # noqa: E402
from breakout.data.store import Store  # noqa: E402
from breakout.paper.tracker import compute_daily_progress  # noqa: E402


def _as_date(v) -> date | None:
    if v is None:
        return None
    try:
        return pd.to_datetime(v).date()
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true", help="Print what would change, write nothing."
    )
    args = ap.parse_args()

    workbook = load_config().paths.workbook
    if not Path(workbook).exists():
        print(f"no workbook at {workbook} — nothing to backfill")
        return 0

    store = Store(workbook)
    trades = store.read_paper_trades_by_state()
    if not trades:
        print("paper_trades is empty — nothing to backfill")
        store.close()
        return 0

    filled = skipped = 0
    for trade in trades:
        if trade.get("entry_date") is None or trade.get("entry_price") is None:
            skipped += 1
            continue
        prices = store.read_prices(trade["symbol"])
        # Closed trades stop at their exit; open trades run to the last bar.
        upto = _as_date(trade.get("exit_date"))
        label, moves = compute_daily_progress(
            trade["entry_date"], trade["entry_price"], prices, upto
        )
        if label is None:
            print(f"  #{trade['id']:>4} {trade['symbol']:<14} no cached bars from entry")
            skipped += 1
            continue
        print(f"  #{trade['id']:>4} {trade['symbol']:<14} {label:<5} {moves}")
        if not args.dry_run:
            store.update_paper_trade(
                trade["id"], days_in_trade=label, daily_moves=moves
            )
        filled += 1

    if args.dry_run:
        print(f"\ndry run: {filled} trade(s) would be filled, {skipped} skipped")
        return 0
    store.close()
    print(f"\nbackfilled {filled} trade(s), {skipped} skipped -> {workbook}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
