# filepath: tests/data_system/normalize/test_tushare.py
"""Tushare processed-field normalization tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.data_system.normalize.tushare import normalize_tushare


def test_trade_calendar_normalizes_one_multirow_batch(tmp_path: Path) -> None:
    input_file = tmp_path / "trade_calendar.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "exchange": ["SSE", "SSE", "SSE"],
                    "cal_date": ["20260101", "20260102", "20260103"],
                    "is_open": [0, 1, 1],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        raw_object="trade_calendar",
        target_name="trade_calendar",
    )

    assert output.table.to_pydict() == {
        "trade_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "is_open": [False, True, True],
    }


def test_stock_basic_normalizes_available_list_dates_and_maps_others_to_null(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "stock_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "301583.SZ"],
                    "list_date": ["19910403", "0"],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        raw_object="stock_basic",
        target_name="stock_basic",
        trade_date="2026-07-03",
    )

    assert output.table["symbol"].to_pylist() == ["000001", "301583"]
    assert output.table["list_date"].to_pylist() == ["1991-04-03", None]


def test_daily_basic_normalizes_without_limit_status(tmp_path: Path) -> None:
    input_file = tmp_path / "daily_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20260703", "20260703"],
                    "turnover_rate": [1.2, 2.3],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        raw_object="daily_basic",
        target_name="daily_basic",
        trade_date="2026-07-03",
    )

    assert output.table.column_names == [
        "ts_code",
        "trade_date",
        "turnover_rate",
        "symbol",
    ]
    assert output.table["trade_date"].to_pylist() == [
        "2026-07-03",
        "2026-07-03",
    ]
    assert output.table["symbol"].to_pylist() == ["000001", "000002"]
