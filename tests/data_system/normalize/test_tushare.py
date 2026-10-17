# filepath: tests/data_system/normalize/test_tushare.py
"""Tushare processed-field normalization tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.data_system.normalize.tushare import normalize_tushare


@pytest.mark.parametrize("empty", [False, True])
def test_distribution_normalization_preserves_missing_cash_and_explicit_registration_date(tmp_path: Path, empty: bool) -> None:
    raw = pd.DataFrame() if empty else pd.DataFrame({"ts_code": ["300908.SZ"], "end_date": ["20260630"],
        "ann_date": ["20260801"], "div_proc": ["实施 "], "imp_ann_date": ["20260904"], "record_date": ["20260911"],
        "ex_date": ["20260914"], "pay_date": [None], "div_listdate": ["20260914"],
        "cash_div_tax": [None], "stk_div": [0.45]})
    path = tmp_path / "raw.parquet"
    pq.write_table(pa.Table.from_pandas(raw, preserve_index=False), path)
    output = normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
                              raw_object="dividend", target_name="dividend", trade_date="2026-09-11").table
    assert output.column_names == ["ts_code", "symbol", "end_date", "ann_date", "div_proc", "imp_ann_date", "record_date", "ex_date",
                                   "pay_date", "div_listdate", "cash_div_tax", "stk_div", "source_row_id"]
    assert output.num_rows == (0 if empty else 1)
    if not empty:
        row = output.to_pylist()[0]
        source_id = row.pop("source_row_id")
        assert len(source_id) == 64
        assert row == {"ts_code": "300908.SZ", "ann_date": "2026-08-01", "symbol": "300908", "end_date": "2026-06-30", "div_proc": "实施",
            "imp_ann_date": "2026-09-04", "record_date": "2026-09-11", "ex_date": "2026-09-14",
            "pay_date": None, "div_listdate": "2026-09-14", "cash_div_tax": None, "stk_div": "0.45"}


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


def test_stock_basic_normalizes_a_columnless_empty_response(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "stock_basic.parquet"
    pq.write_table(
        pa.Table.from_pandas(pd.DataFrame(), preserve_index=False),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        raw_object="stock_basic",
        target_name="stock_basic",
        trade_date="2019-04-01",
    )

    assert output.table.num_rows == 0
    assert output.table.column_names == ["symbol", "list_date"]


@pytest.mark.parametrize("target_name", ("stock_st", "suspend_d"))
def test_empty_symbol_sets_normalize_a_columnless_response(
    tmp_path: Path,
    target_name: str,
) -> None:
    input_file = tmp_path / f"{target_name}.parquet"
    pq.write_table(
        pa.Table.from_pandas(pd.DataFrame(), preserve_index=False),
        input_file,
    )

    output = normalize_tushare(
        input_file=input_file,
        output_name=tmp_path / "output.parquet",
        raw_object=target_name,
        target_name=target_name,
        trade_date="2019-04-01",
    )

    assert output.table.num_rows == 0
    assert output.table.column_names == ["symbol"]


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
