r"""Reset the scanner workbook to empty sheets — a clean slate for a fresh run.

    .\.venv\Scripts\python.exe scripts\reset_workbook.py              # all sheets
    .\.venv\Scripts\python.exe scripts\reset_workbook.py --keep-prices
    .\.venv\Scripts\python.exe scripts\reset_workbook.py --sheets paper_trades,run_log
    .\.venv\Scripts\python.exe scripts\reset_workbook.py --yes        # skip the prompt

The existing workbook is copied to `breakout.backup-<timestamp>.xlsx` beside it
before anything is cleared, so a mistaken reset is recoverable. Sheet headers are
always preserved — the sheets are emptied, never dropped, so `Store` opens the
result without having to rebuild the schema.

Note on `--keep-prices`: the price cache is only reused when it is less than a
day old, so keeping a stale cache saves no fetching on the next scan. It is worth
keeping only if you want the old bars around to inspect.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from breakout.config import load_config  # noqa: E402
from breakout.data.store import _SCHEMAS  # noqa: E402


def _summarize(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    return {name: len(df) for name, df in sheets.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset the scanner workbook.")
    ap.add_argument(
        "--sheets",
        help="Comma-separated sheets to clear (default: all).",
    )
    ap.add_argument(
        "--keep-prices",
        action="store_true",
        help="Clear everything except the cached OHLCV bars.",
    )
    ap.add_argument("--yes", action="store_true", help="Don't prompt for confirmation.")
    ap.add_argument("--no-backup", action="store_true", help="Skip the backup copy.")
    args = ap.parse_args()

    workbook = load_config().paths.workbook

    if args.sheets:
        targets = [s.strip() for s in args.sheets.split(",") if s.strip()]
        unknown = [s for s in targets if s not in _SCHEMAS]
        if unknown:
            print(f"unknown sheet(s): {', '.join(unknown)}")
            print(f"valid sheets: {', '.join(_SCHEMAS)}")
            return 2
    elif args.keep_prices:
        targets = [s for s in _SCHEMAS if s != "prices"]
    else:
        targets = list(_SCHEMAS)

    counts = _summarize(workbook)
    if not counts:
        print(f"no workbook at {workbook} — nothing to reset")
        return 0

    print(f"workbook: {workbook}")
    for name in _SCHEMAS:
        mark = "CLEAR" if name in targets else "keep "
        print(f"  [{mark}] {name:22s} {counts.get(name, 0):>7} rows")

    if not args.yes:
        if input("\nproceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("aborted")
            return 1

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = workbook.with_name(f"{workbook.stem}.backup-{stamp}.xlsx")
        shutil.copy2(workbook, backup)
        print(f"backup: {backup}")

    # Read every sheet, blank the targeted ones, rewrite the whole workbook so
    # the untouched sheets survive verbatim.
    existing = pd.read_excel(workbook, sheet_name=None, engine="openpyxl")
    tmp = workbook.with_name(workbook.name + ".tmp")
    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        for name, cols in _SCHEMAS.items():
            if name in targets:
                df = pd.DataFrame(columns=cols)
            else:
                df = existing.get(name, pd.DataFrame(columns=cols))
                for c in cols:
                    if c not in df.columns:
                        df[c] = None
                df = df[cols]
            df.to_excel(writer, sheet_name=name, index=False)
    tmp.replace(workbook)

    print(f"cleared: {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
