# filepath: tests/data_system/normalize/test_symbol_index_engine.py
"""Tests for canonical Level-2 symbol slices."""

from __future__ import annotations

import pyarrow as pa
import pytest

from src.data_system.normalize.symbol_index_engine import SymbolIndexEngine


def test_execute_sorts_by_symbol_and_timestamp_and_returns_ranges() -> None:
    table = pa.table(
        {
            "symbol": ["600000", "000001", "600000"],
            "ts_utc": [2, 1, 1],
        }
    )

    sorted_table, symbol_slices = SymbolIndexEngine.execute(table)

    assert sorted_table["symbol"].to_pylist() == ["000001", "600000", "600000"]
    assert sorted_table["ts_utc"].to_pylist() == [1, 1, 2]
    assert symbol_slices == {
        "000001": range(0, 1),
        "600000": range(1, 3),
    }


@pytest.mark.parametrize("symbol", ["", None])
def test_execute_rejects_missing_symbol_identity(symbol: str | None) -> None:
    table = pa.table(
        {
            "symbol": pa.array([symbol], type=pa.string()),
            "ts_utc": [1],
        }
    )

    with pytest.raises(ValueError, match="non-empty string"):
        SymbolIndexEngine.execute(table)
