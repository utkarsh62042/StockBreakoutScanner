"""Writing text into columns that are still blank on disk.

A column that has never held a value round-trips through Excel as all-NaN
float64. pandas >=2.1 refuses to store a string in a float column, which took
down `eod_settle` the first time it tried to stamp `days_in_trade` ("D0") onto
a workbook whose progress columns were empty.
"""

from __future__ import annotations

from breakout.data.store import Store


def _fresh(tmp_path):
    """A workbook saved with one paper trade and one run, both left with their
    text columns blank — exactly how a first-run workbook comes off disk."""
    wb = tmp_path / "breakout.xlsx"
    with Store(wb) as store:
        trade_id = store.insert_paper_trade(
            {"symbol": "ACME", "state": "ENTERED", "entry_price": 100.0}
        )
        run_id = store.start_run("eod_settle")
        store.finish_run(run_id, "SUCCESS")
    return wb, trade_id, run_id


def test_progress_columns_accept_text_when_blank_on_disk(tmp_path) -> None:
    wb, trade_id, _ = _fresh(tmp_path)
    with Store(wb) as store:
        store.update_paper_trade(trade_id, days_in_trade="D0", daily_moves=None)
        store.update_paper_trade(trade_id, days_in_trade="D1", daily_moves="D1:4.0%")

    with Store(wb) as store:
        (trade,) = store.read_paper_trades_by_state("ENTERED")
    assert trade["days_in_trade"] == "D1"
    assert trade["daily_moves"] == "D1:4.0%"


def test_run_log_records_an_error_message_when_blank_on_disk(tmp_path) -> None:
    # The failure handler in every job calls finish_run(..., error_message=...);
    # it must not raise on top of the error it is reporting. Finish a run that
    # came off disk, so nothing has widened `error_message` this session.
    wb, _, run_id = _fresh(tmp_path)
    with Store(wb) as store:
        store.finish_run(run_id, "FAILED", error_message="boom")
