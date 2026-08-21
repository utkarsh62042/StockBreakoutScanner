"""Excel-workbook persistence layer.

Every long-lived piece of state the scanner produces lives in a single
`.xlsx` workbook, one sheet per logical table: the price cache, NIFTY 500
universe, both watchlists, the paper-trade audit log, failed breakouts, and
the per-job run log. The user asked for a spreadsheet they can open directly
(a SQL backend may return later); Excel is used rather than CSV because CSV
has no notion of multiple sheets.

Design:
    - On open, the workbook is loaded fully into memory as one DataFrame per
      sheet (each with a fixed column schema, so empty sheets still carry
      headers). If the file doesn't exist yet, all sheets start empty.
    - Mutating methods edit the in-memory DataFrames only. Nothing touches
      disk until `close()` (or an explicit `save()` / `transaction()` exit).
      This is deliberate: `morning_scan` calls `upsert_prices` ~500 times in
      a loop, and rewriting a 150k-row workbook on every call would be far
      too slow. Jobs always use `Store` as a context manager, so the single
      flush on `__exit__` persists everything.
    - Read methods return `list[dict]` (or a DataFrame for prices) with blank
      cells normalised to `None` — Excel round-trips missing values as NaN,
      but the rest of the codebase relies on `None` (e.g.
      `trade.get("target_1") is not None`).

Trade-off vs. the old SQLite backend: durability. A hard crash mid-run loses
the current run's writes. That's acceptable for a re-runnable batch tool.
"""

from __future__ import annotations

import logging
import math
import os
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd


logger = logging.getLogger(__name__)


# ── Sheet schemas ─────────────────────────────────────────────────────────
# Column order is authoritative: it defines how each sheet is laid out in the
# workbook and guarantees empty sheets still carry headers.

_SCHEMAS: dict[str, list[str]] = {
    "prices": ["symbol", "date", "open", "high", "low", "close", "volume"],
    "universe": [
        "symbol", "company_name", "sector", "industry", "market_cap_cr",
        "last_updated",
    ],
    "setup_watchlist": [
        "symbol", "pattern", "breakout_level", "score", "detected_date",
        "base_height", "notes",
    ],
    "pullback_watchlist": [
        "symbol", "breakout_date", "breakout_level", "original_score", "notes",
    ],
    "paper_trades": [
        "id", "symbol", "pattern", "alert_date", "alert_type", "score",
        "state", "entry_date", "entry_price", "stop_loss", "target_1",
        "target_2", "exit_date", "exit_price", "shares", "pnl_inr", "pnl_r",
        "days_held", "max_favorable", "max_adverse", "notes",
    ],
    "failed_breakouts": [
        "symbol", "breakout_date", "failure_date", "pattern", "original_score",
    ],
    "run_log": [
        "id", "job_name", "started_at", "finished_at", "status",
        "alerts_generated", "error_message",
    ],
}

# Columns holding ISO date/timestamp strings. On load we coerce these back to
# plain strings so downstream `pd.to_datetime(...)` / date math is stable even
# if Excel typed a cell as a datetime.
_DATE_COLS = {
    "date", "last_updated", "detected_date", "breakout_date", "failure_date",
    "alert_date", "entry_date", "exit_date", "started_at", "finished_at",
}

# Integer-valued columns — coerced to int (not float) in returned records so
# ids and counts read cleanly.
_INT_COLS = {"id", "shares", "days_held", "alerts_generated", "volume"}

# Transient-lock retry policy. This project's data_cache lives under a OneDrive
# folder, and OneDrive (or Excel, if the user has the workbook open) can hold a
# brief exclusive lock while syncing a freshly-written file. Retry a few times
# with linear backoff before giving up.
_LOCK_RETRIES = 6
_LOCK_RETRY_SLEEP = 1.0  # seconds; multiplied by attempt number


class Store:
    """In-memory-backed wrapper over an Excel workbook exposing the operations
    the rest of the codebase needs. Always use it as a context manager (or call
    `.close()` explicitly) so the workbook is flushed to disk on exit.
    """

    def __init__(self, workbook_path: Path):
        self.workbook_path = Path(workbook_path)
        self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        self._sheets: dict[str, pd.DataFrame] = self._load()

    # ── Load / save ──────────────────────────────────────────────────────

    def _load(self) -> dict[str, pd.DataFrame]:
        sheets: dict[str, pd.DataFrame] = {}
        existing: dict[str, pd.DataFrame] = {}
        if self.workbook_path.exists():
            # A file that exists but can't be read is a hard error — almost
            # always a transient lock (OneDrive sync / Excel open). We must NOT
            # fall back to empty sheets: a job would then see no data and could
            # persist that emptiness over real state. Retry, then fail loudly.
            last_err: Exception | None = None
            for attempt in range(_LOCK_RETRIES):
                try:
                    existing = pd.read_excel(
                        self.workbook_path, sheet_name=None, engine="openpyxl"
                    )
                    last_err = None
                    break
                except PermissionError as e:
                    last_err = e
                    time.sleep(_LOCK_RETRY_SLEEP * (attempt + 1))
                except Exception as e:
                    # Corrupt/unreadable for a non-lock reason — don't retry.
                    raise RuntimeError(
                        f"workbook {self.workbook_path} exists but could not be "
                        f"read: {e}"
                    ) from e
            if last_err is not None:
                raise PermissionError(
                    f"workbook {self.workbook_path} is locked and could not be "
                    f"read after {_LOCK_RETRIES} attempts: {last_err}. Close it "
                    f"in Excel, or wait for OneDrive to finish syncing, then "
                    f"re-run. (Tip: exclude data_cache/ from OneDrive sync.)"
                )
        for name, cols in _SCHEMAS.items():
            df = existing.get(name)
            if df is None:
                df = pd.DataFrame(columns=cols)
            else:
                # Guarantee every schema column exists, drop unexpected extras.
                for c in cols:
                    if c not in df.columns:
                        df[c] = None
                df = df[cols]
                df = self._normalize_date_cols(df)
            sheets[name] = df.reset_index(drop=True)
        return sheets

    @staticmethod
    def _normalize_date_cols(df: pd.DataFrame) -> pd.DataFrame:
        """Coerce date columns to `YYYY-MM-DD` (or full ISO) strings."""
        for col in df.columns:
            if col not in _DATE_COLS:
                continue
            def _to_str(v: Any) -> Any:
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return None
                if isinstance(v, (pd.Timestamp, datetime, date)):
                    # 'date' price column is day-granularity; timestamps
                    # (started_at/finished_at) keep their full ISO form.
                    if col in {"started_at", "finished_at"}:
                        return pd.Timestamp(v).isoformat(timespec="seconds")
                    return pd.Timestamp(v).strftime("%Y-%m-%d")
                return str(v)
            df[col] = df[col].map(_to_str)
        return df

    def save(self) -> None:
        """Flush all sheets to the workbook on disk.

        Writes to a sibling temp file first, then atomically replaces the
        target. This keeps a crash mid-write from corrupting the real workbook,
        and lets us retry only the (lock-prone) replace step. If the target
        stays locked past the retry budget, fail loudly with an actionable
        message rather than dropping the run's writes.
        """
        tmp = self.workbook_path.with_name(self.workbook_path.name + ".tmp")
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            for name, cols in _SCHEMAS.items():
                df = self._sheets.get(name)
                if df is None:
                    df = pd.DataFrame(columns=cols)
                df.to_excel(writer, sheet_name=name, index=False)

        last_err: Exception | None = None
        for attempt in range(_LOCK_RETRIES):
            try:
                os.replace(tmp, self.workbook_path)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(_LOCK_RETRY_SLEEP * (attempt + 1))
        # Give up: clean up the temp file, then fail loudly.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise PermissionError(
            f"could not write {self.workbook_path} after {_LOCK_RETRIES} "
            f"attempts: {last_err}. The file is locked — close it in Excel, or "
            f"wait for OneDrive to finish syncing, then re-run. (Tip: exclude "
            f"data_cache/ from OneDrive sync to avoid this.)"
        )

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """Group writes and flush to disk when the block exits cleanly.

        Kept for API compatibility. Internal methods no longer write per-call;
        callers who want an intermediate durable checkpoint can use this.
        """
        yield self
        self.save()

    def close(self) -> None:
        self.save()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        # Flush even on exception — jobs record their failure in run_log first,
        # then re-raise, and we want that row (and any partial work) persisted.
        self.close()

    # ── Prices ───────────────────────────────────────────────────────────

    def upsert_prices(self, symbol: str, df: pd.DataFrame) -> int:
        """Insert or replace OHLCV rows for one symbol. Returns rows written."""
        if df.empty:
            return 0
        rows = []
        for date_val, row in df.iterrows():
            d = (
                date_val.strftime("%Y-%m-%d")
                if hasattr(date_val, "strftime")
                else str(date_val)
            )
            rows.append(
                {
                    "symbol": symbol,
                    "date": d,
                    "open": _safe_float(row.get("open")),
                    "high": _safe_float(row.get("high")),
                    "low": _safe_float(row.get("low")),
                    "close": _safe_float(row.get("close")),
                    "volume": _safe_int(row.get("volume")),
                }
            )
        incoming = pd.DataFrame(rows, columns=_SCHEMAS["prices"])
        existing = self._sheets["prices"]
        # Drop any existing (symbol, date) rows the incoming batch replaces.
        if not existing.empty:
            dup_dates = set(incoming["date"])
            mask = (existing["symbol"] == symbol) & (existing["date"].isin(dup_dates))
            existing = existing[~mask]
        self._sheets["prices"] = pd.concat([existing, incoming], ignore_index=True)
        return len(rows)

    def read_prices(self, symbol: str, lookback_days: int | None = None) -> pd.DataFrame:
        """Return a symbol's price history as a DataFrame indexed by date.

        If `lookback_days` is given, returns only that many most-recent rows.
        """
        df = self._sheets["prices"]
        sub = df[df["symbol"] == symbol].copy()
        cols = ["date", "open", "high", "low", "close", "volume"]
        if sub.empty:
            empty = pd.DataFrame(columns=cols[1:])
            empty.index = pd.DatetimeIndex([], name="date")
            return empty
        # ISO date strings sort chronologically as plain text.
        sub = sub.sort_values("date")
        if lookback_days is not None:
            sub = sub.iloc[-lookback_days:]
        sub = sub[cols].copy()
        sub["date"] = pd.to_datetime(sub["date"])
        sub = sub.set_index("date")
        for c in ("open", "high", "low", "close", "volume"):
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        return sub

    def latest_price_date(self, symbol: str) -> date | None:
        df = self._sheets["prices"]
        sub = df[df["symbol"] == symbol]
        if sub.empty:
            return None
        return pd.to_datetime(sub["date"].max()).date()

    # ── Universe ─────────────────────────────────────────────────────────

    def upsert_universe(self, rows: list[dict]) -> int:
        """Replace the universe sheet with the given rows."""
        if not rows:
            return 0
        today = date.today().isoformat()
        records = [
            {
                "symbol": r["symbol"],
                "company_name": r.get("company_name"),
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "market_cap_cr": r.get("market_cap_cr"),
                "last_updated": today,
            }
            for r in rows
        ]
        self._sheets["universe"] = pd.DataFrame(records, columns=_SCHEMAS["universe"])
        return len(records)

    def read_universe(self) -> list[dict]:
        return _records(self._sheets["universe"].sort_values("symbol"))

    def universe_age_days(self) -> int | None:
        df = self._sheets["universe"]
        if df.empty or df["last_updated"].dropna().empty:
            return None
        last = pd.to_datetime(df["last_updated"].max()).date()
        return (date.today() - last).days

    # ── Watchlists ───────────────────────────────────────────────────────

    def replace_setup_watchlist(self, rows: list[dict]) -> None:
        """Atomically replace the setup watchlist with today's findings."""
        records = [
            {
                "symbol": r["symbol"],
                "pattern": r.get("pattern"),
                "breakout_level": r.get("breakout_level"),
                "score": r.get("score"),
                "detected_date": r.get("detected_date") or date.today().isoformat(),
                "base_height": r.get("base_height"),
                "notes": r.get("notes"),
            }
            for r in rows
        ]
        self._sheets["setup_watchlist"] = pd.DataFrame(
            records, columns=_SCHEMAS["setup_watchlist"]
        )

    def read_setup_watchlist(self) -> list[dict]:
        df = self._sheets["setup_watchlist"]
        if not df.empty:
            df = df.sort_values("score", ascending=False)
        return _records(df)

    def upsert_pullback_watchlist(self, rows: list[dict]) -> None:
        """Add or refresh pullback watchlist entries (rolling — never replace)."""
        if not rows:
            return
        records = [
            {
                "symbol": r["symbol"],
                "breakout_date": r.get("breakout_date"),
                "breakout_level": r.get("breakout_level"),
                "original_score": r.get("original_score"),
                "notes": r.get("notes"),
            }
            for r in rows
        ]
        incoming = pd.DataFrame(records, columns=_SCHEMAS["pullback_watchlist"])
        existing = self._sheets["pullback_watchlist"]
        if not existing.empty:
            existing = existing[~existing["symbol"].isin(incoming["symbol"])]
        self._sheets["pullback_watchlist"] = pd.concat(
            [existing, incoming], ignore_index=True
        )

    def prune_pullback_watchlist(self, older_than_days: int) -> int:
        """Remove pullback entries whose breakout date is > N days old."""
        df = self._sheets["pullback_watchlist"]
        if df.empty:
            return 0
        cutoff = (date.today() - pd.Timedelta(days=older_than_days)).isoformat()
        keep = df["breakout_date"].fillna("") >= cutoff
        removed = int((~keep).sum())
        self._sheets["pullback_watchlist"] = df[keep].reset_index(drop=True)
        return removed

    def read_pullback_watchlist(self) -> list[dict]:
        return _records(self._sheets["pullback_watchlist"])

    def remove_pullback(self, symbol: str) -> int:
        """Drop a symbol from the pullback watchlist (e.g. its breakout failed)."""
        df = self._sheets["pullback_watchlist"]
        if df.empty:
            return 0
        keep = df["symbol"] != symbol
        removed = int((~keep).sum())
        self._sheets["pullback_watchlist"] = df[keep].reset_index(drop=True)
        return removed

    # ── Paper trades ─────────────────────────────────────────────────────

    def insert_paper_trade(self, row: dict) -> int:
        """Insert a new paper trade. Returns the assigned trade id."""
        trade_id = self._next_id("paper_trades")
        record = {c: None for c in _SCHEMAS["paper_trades"]}
        record.update({k: v for k, v in row.items() if k in record})
        record["id"] = trade_id
        self._sheets["paper_trades"] = pd.concat(
            [self._sheets["paper_trades"], pd.DataFrame([record], columns=_SCHEMAS["paper_trades"])],
            ignore_index=True,
        )
        return trade_id

    def update_paper_trade(self, trade_id: int, **fields: Any) -> None:
        if not fields:
            return
        df = self._sheets["paper_trades"]
        mask = df["id"] == trade_id
        for k, v in fields.items():
            if k in df.columns:
                # `.loc` with a scalar assigns to every matched row.
                df.loc[mask, k] = v

    def read_paper_trades_by_state(self, *states: str) -> list[dict]:
        df = self._sheets["paper_trades"]
        if states:
            df = df[df["state"].isin(states)]
        return _records(df)

    # ── Failed breakouts ─────────────────────────────────────────────────

    def insert_failed_breakout(self, row: dict) -> None:
        record = {
            "symbol": row["symbol"],
            "breakout_date": row["breakout_date"],
            "failure_date": row["failure_date"],
            "pattern": row.get("pattern"),
            "original_score": row.get("original_score"),
        }
        df = self._sheets["failed_breakouts"]
        # Upsert on (symbol, breakout_date).
        if not df.empty:
            mask = (df["symbol"] == record["symbol"]) & (
                df["breakout_date"] == record["breakout_date"]
            )
            df = df[~mask]
        self._sheets["failed_breakouts"] = pd.concat(
            [df, pd.DataFrame([record], columns=_SCHEMAS["failed_breakouts"])],
            ignore_index=True,
        )

    # ── Run log ──────────────────────────────────────────────────────────

    def start_run(self, job_name: str) -> int:
        run_id = self._next_id("run_log")
        record = {c: None for c in _SCHEMAS["run_log"]}
        record.update(
            {
                "id": run_id,
                "job_name": job_name,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "status": "RUNNING",
            }
        )
        self._sheets["run_log"] = pd.concat(
            [self._sheets["run_log"], pd.DataFrame([record], columns=_SCHEMAS["run_log"])],
            ignore_index=True,
        )
        return run_id

    def finish_run(
        self,
        run_id: int,
        status: str,
        alerts_generated: int = 0,
        error_message: str | None = None,
    ) -> None:
        df = self._sheets["run_log"]
        mask = df["id"] == run_id
        df.loc[mask, "finished_at"] = datetime.now().isoformat(timespec="seconds")
        df.loc[mask, "status"] = status
        df.loc[mask, "alerts_generated"] = alerts_generated
        df.loc[mask, "error_message"] = error_message

    # ── Internals ─────────────────────────────────────────────────────────

    def _next_id(self, sheet: str) -> int:
        df = self._sheets[sheet]
        if df.empty or df["id"].dropna().empty:
            return 1
        return int(pd.to_numeric(df["id"], errors="coerce").max()) + 1


def _records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to a list of dicts with Excel-friendly cleanup:
    NaN/NaT become None, and known integer columns are returned as ints.
    """
    out: list[dict] = []
    for rec in df.to_dict("records"):
        clean: dict = {}
        for k, v in rec.items():
            if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
                clean[k] = None
            elif k in _INT_COLS:
                try:
                    clean[k] = int(v)
                except (TypeError, ValueError):
                    clean[k] = v
            else:
                clean[k] = v
        out.append(clean)
    return out


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    try:
        i = int(v)
        return i
    except (TypeError, ValueError):
        return None
