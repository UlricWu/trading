# filepath: tests/data_system/normalize/test_profiles.py
"""Tushare processed-field normalization tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.data_system.normalize.profiles import normalize_tushare


def test_stock_basic_normalizes_list_date_for_access(tmp_path: Path) -> None:
    input_file = tmp_path / "stock_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "list_date": ["19910403"],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        target_name="stock_basic",
    )

    assert output.table["symbol"].to_pylist() == ["000001"]
    assert output.table["list_date"].to_pylist() == ["1991-04-03"]


def test_daily_basic_normalizes_limit_status_for_access(tmp_path: Path) -> None:
    input_file = tmp_path / "daily_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20260506", "20260506"],
                    "limit_status": ["2", "3"],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        target_name="daily_basic",
    )

    assert output.table["trade_date"].to_pylist() == [
        "2026-05-06",
        "2026-05-06",
    ]
    assert output.table["limit_status"].type == pa.int64()
    assert output.table["limit_status"].to_pylist() == [2, 3]


@pytest.mark.parametrize("limit_status", [None, True, 2.5, 7])
def test_daily_basic_rejects_invalid_limit_status(
    tmp_path: Path,
    limit_status: object,
) -> None:
    input_file = tmp_path / "daily_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "limit_status": [limit_status],
                }
            ),
            preserve_index=False,
        ),
        input_file,
    )

    with pytest.raises(ValueError, match="limit_status"):
        normalize_tushare(
            input_file=input_file,
            output_name=tmp_path / "output.parquet",
            target_name="daily_basic",
        )
