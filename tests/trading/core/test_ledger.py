# filepath: tests/trading/core/test_ledger.py
from src.trading.core.ledger import ExecutionLedger


def test_order_metadata_defaults_and_input_mappings_are_independently_owned() -> None:
    ledger = ExecutionLedger()
    metadata = {"strategy": "threshold"}
    ledger.record_order_submit(ts_us=1, symbol="A", side="BUY", qty=100, order_id=1)
    ledger.record_order_reject(ts_us=1, symbol="A", side="BUY", qty=100, order_id=1,
                               reason="insufficient_cash")
    ledger.record_order_submit(ts_us=2, symbol="B", side="BUY", qty=100, order_id=2, meta=metadata)
    ledger.record_order_reject(ts_us=2, symbol="B", side="BUY", qty=100, order_id=2,
                               reason="insufficient_cash", meta=metadata)

    metadata["strategy"] = "changed"
    ledger.records[0]["meta"]["source"] = "local"

    assert ledger.records[1]["meta"] == {}
    assert ledger.records[2]["meta"] == ledger.records[3]["meta"] == {"strategy": "threshold"}
    assert ledger.reject_reason_total() == 2
    assert ledger.reject_reason_topn() == [("insufficient_cash", 2)]
