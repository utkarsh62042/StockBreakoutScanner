r"""Rebuild the price cache on a split/bonus-adjusted basis.

    .\.venv\Scripts\python.exe scripts\refetch_prices.py --dry-run
    .\.venv\Scripts\python.exe scripts\refetch_prices.py

Run this once after the upgrade that switched the fetcher to adjusted bars, and
any time you suspect the cache has drifted off a single adjustment basis.

`Store.upsert_prices` re-bases the cache whenever the feed's basis changes,
which keeps things coherent from here on, but it cannot repair a *cliff already
sitting inside* the cached window — the two sides of it are on different bases,
so no single factor fixes both. Dropping those bars and refetching is the only
clean remedy.

Default behaviour is to repair only what is actually broken: symbols whose
cached series contains an implausible one-day move. `--all` rebuilds everything,
which is slower but leaves no doubt.

Expect some symbols to report "still discontinuous after refetch". That is not
a failure of this script: Yahoo does not model demergers or capital reductions
as splits, so no refetch can clear them, and the same goes for a genuine
one-day crash. Those symbols stay excluded by `data.validate` until the bar
ages out of the analysis window, which is the intended outcome in both cases.

Open paper trades are left untouched. Their levels were set from unadjusted
prices, so if one of your open positions has gone ex-split, check that row by
hand — the script prints a warning naming any it finds.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from breakout.config import ensure_runtime_dirs, load_config  # noqa: E402
from breakout.data.fetcher import FetchError, RateLimitError, make_fetcher  # noqa: E402
from breakout.data.store import Store  # noqa: E402
from breakout.data.validate import find_price_discontinuity  # noqa: E402
from breakout.logging_setup import setup_logging  # noqa: E402
from breakout.paper.tracker import OPEN_STATES  # noqa: E402


logger = logging.getLogger("refetch_prices")

_HISTORY_DAYS = 300   # matches morning_scan's analysis window


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refetch cached prices on an adjusted basis"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="rebuild every symbol, not just the ones with a detected cliff",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be refetched and change nothing",
    )
    args = parser.parse_args()

    cfg = load_config()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.paths.logs, level=cfg.logging.level, console=cfg.logging.console)

    with Store(cfg.paths.workbook) as store:
        universe = [u["symbol"] for u in store.read_universe()]
        if not universe:
            logger.error("universe is empty — run morning_scan first")
            return 1

        # Which symbols need work?
        if args.all:
            targets = [s for s in universe if len(store.read_prices(s))]
            logger.info(f"--all: rebuilding {len(targets)} symbols")
        else:
            targets = []
            for sym in universe:
                found = find_price_discontinuity(store.read_prices(sym))
                if found is not None:
                    when, move = found
                    logger.info(f"{sym}: {move * 100:+.1f}% on {when}")
                    targets.append(sym)
            logger.info(
                f"{len(targets)} of {len(universe)} symbols carry a discontinuity"
            )

        if not targets:
            logger.info("nothing to do")
            return 0

        # Warn about open positions in affected names before touching anything.
        open_trades = store.read_paper_trades_by_state(*OPEN_STATES)
        affected = sorted({t["symbol"] for t in open_trades} & set(targets))
        if affected:
            logger.warning(
                "open paper trade(s) on rebuilt symbols — their entry/stop/target "
                f"levels are on the old basis, verify by hand: {', '.join(affected)}"
            )

        if args.dry_run:
            logger.info(f"dry run — would refetch {len(targets)} symbols")
            return 0

        fetcher = make_fetcher(cfg)
        rebuilt = 0
        for n, sym in enumerate(targets, 1):
            try:
                df = fetcher.fetch_history(sym, days=_HISTORY_DAYS)
            except RateLimitError as e:
                # Stop cleanly: the symbols done so far are already consistent,
                # and a rerun picks up where this left off.
                logger.error(f"stopping after {rebuilt} symbols: {e}")
                break
            except FetchError as e:
                logger.warning(f"skip {sym}: {e}")
                continue
            # Drop first so the fresh frame can't be re-based against the stale
            # one — the point here is to discard the old basis, not reconcile it.
            store.delete_prices(sym)
            store.upsert_prices(sym, df)
            rebuilt += 1
            if n % 25 == 0:
                logger.info(f"{n}/{len(targets)} …")

            left = find_price_discontinuity(store.read_prices(sym))
            if left is not None:
                logger.warning(
                    f"{sym}: still discontinuous after refetch ({left[1] * 100:+.1f}% "
                    f"on {left[0]}) — the feed itself has not adjusted this one"
                )

        logger.info(f"rebuilt {rebuilt} symbols on an adjusted basis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
