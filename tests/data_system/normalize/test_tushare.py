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
                                   "pay_date", "div_listdate", "cash_div_tax", "stk_div", "base_date", "base_share"]
    assert output.num_rows == (0 if empty else 1)
    if not empty:
        row = output.to_pylist()[0]
        assert row == {"ts_code": "300908.SZ", "ann_date": "2026-08-01", "symbol": "300908", "end_date": "2026-06-30", "div_proc": "实施",
            "imp_ann_date": "2026-09-04", "record_date": "2026-09-11", "ex_date": "2026-09-14",
            "pay_date": None, "div_listdate": "2026-09-14", "cash_div_tax": None, "stk_div": "0.45",
            "base_date": None, "base_share": None}


def test_dividend_base_shares_do_not_rescale_per_share_distribution_terms(tmp_path: Path) -> None:
    path = tmp_path / "raw.parquet"
    pd.DataFrame({"ts_code": ["300908.SZ"], "end_date": ["20260630"], "ann_date": ["20260801"],
        "div_proc": ["实施"], "imp_ann_date": ["20260904"], "record_date": ["20260911"],
        "ex_date": ["20260914"], "pay_date": ["20260914"], "div_listdate": ["20260914"],
        "cash_div_tax": ["0.12345678"], "stk_div": ["0.45"], "base_date": ["20260911"],
        "base_share": [12345.6789]}).to_parquet(path, index=False)
    table = normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
        raw_object="dividend", target_name="dividend", trade_date="2026-09-11").table
    assert table["base_share"].to_pylist() == pytest.approx([123456789])
    assert table.schema.field("base_share").type == pa.float64()
    assert table["base_date"].to_pylist() == ["2026-09-11"]
    assert table["cash_div_tax"].to_pylist() == ["0.12345678"]
    assert table["stk_div"].to_pylist() == ["0.45"]


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
    assert output.table["turnover_rate"].to_pylist() == pytest.approx([0.012, 0.023])


def test_daily_bar_normalizes_units_without_rounding_prices_or_filling_unknowns(tmp_path: Path) -> None:
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"], "trade_date": ["20260720"] * 2,
        "open": [10.123, 20.456], "high": [10.125, 20.457], "low": [10.121, 20.455],
        "close": [10.124, 20.456], "pre_close": [10.1, 20.5], "change": [0.024, -0.044],
        "pct_chg": [2.5, -1.25], "vol": [13.01, 0.29], "amount": [123.456, 0.0],
        "ah_vol": [1.01, None], "ah_amount": [1.234567, None],
    })
    path = tmp_path / "raw.parquet"
    raw.to_parquet(path, index=False)
    before = path.read_bytes()
    table = normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
        raw_object="daily_bar", target_name="daily_bar", trade_date="2026-07-20").table
    assert path.read_bytes() == before
    assert table["vol"].to_pylist() == [1301, 29]
    assert table["ah_vol"].to_pylist() == [101, None]
    assert table.schema.field("vol").type == pa.int64()
    assert table.schema.field("ah_vol").type == pa.int64()
    assert table["amount"].to_pylist() == pytest.approx([123456, 0])
    assert table["ah_amount"].to_pylist() == [pytest.approx(1234.567), None]
    assert table["pct_chg"].to_pylist() == pytest.approx([0.025, -0.0125])
    for column in ("open", "high", "low", "close", "pre_close", "change"):
        assert table[column].to_pylist() == raw[column].tolist()


@pytest.mark.parametrize("volume", ["1.001", "1e-20", "-1", "inf", "100000000000000000"])
def test_daily_bar_rejects_unrepresentable_share_counts(tmp_path: Path, volume: str) -> None:
    path = tmp_path / "raw.parquet"
    pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260720"],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "pre_close": [10.0], "change": [0.0], "pct_chg": [0.0], "amount": [1.0],
        "vol": [volume]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="daily_bar.vol"):
        normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
            raw_object="daily_bar", target_name="daily_bar", trade_date="2026-07-20")


def test_daily_bar_keeps_missing_after_hours_and_exact_large_share_counts(tmp_path: Path) -> None:
    path = tmp_path / "raw.parquet"
    pd.DataFrame({"ts_code": ["600000.SH", "000001.SZ"], "trade_date": ["20260720"] * 2,
        "open": [10.0] * 2, "high": [10.0] * 2, "low": [10.0] * 2, "close": [10.0] * 2,
        "pre_close": [10.0] * 2, "change": [0.0] * 2, "pct_chg": [0.0] * 2, "amount": [1.0] * 2,
        "vol": ["9.223372036854775807e16", None]}).to_parquet(path, index=False)
    table = normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
        raw_object="daily_bar", target_name="daily_bar", trade_date="2026-07-20").table
    assert table["vol"].to_pylist() == [9223372036854775807, None]
    assert table["ah_vol"].to_pylist() == [None, None]
    assert table["ah_amount"].to_pylist() == [None, None]


@pytest.mark.parametrize("amount", ["invalid", float("inf"), 1e308])
def test_normalization_rejects_invalid_or_overflowed_scaled_amounts(tmp_path: Path, amount: str | float) -> None:
    path = tmp_path / "raw.parquet"
    pd.DataFrame({"ts_code": ["000001.SZ"], "net_mf_amount": [amount]}).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="moneyflow.net_mf_amount"):
        normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
            raw_object="moneyflow", target_name="moneyflow", trade_date="2026-07-20")


@pytest.mark.parametrize("target,fields,expected", [
    ("daily_basic", {"total_share": 12345.6789, "float_share": 12.5, "free_share": None,
        "total_mv": 12.3456, "circ_mv": 9.8765, "turnover_rate": 250.0,
        "turnover_rate_f": 2.5, "dv_ratio": 3.0, "dv_ttm": None,
        "volume_ratio": 2.5, "pe": 30.0, "close": 10.123},
        {"total_share": 123456789, "float_share": 125000, "free_share": None,
         "total_mv": 123456, "circ_mv": 98765, "turnover_rate": 2.5,
         "turnover_rate_f": 0.025, "dv_ratio": 0.03, "dv_ttm": None,
         "volume_ratio": 2.5, "pe": 30.0, "close": 10.123}),
    ("stock_basic", {"total_share": 1.23456789, "float_share": 0.00125,
        "rev_yoy": -12.5, "profit_yoy": 250.0, "gpr": 30.0, "npr": 10.0,
        "reserved": 12.3, "total_assets": 2.0, "pe": 30.0},
        {"total_share": 123456789, "float_share": 125000,
         "rev_yoy": -0.125, "profit_yoy": 2.5, "gpr": 0.3, "npr": 0.1,
         "reserved": 12.3, "total_assets": 2.0, "pe": 30.0}),
    ("moneyflow", {"buy_sm_vol": 13, "sell_elg_vol": 2, "net_mf_vol": -3,
        "buy_sm_amount": 1.25, "sell_elg_amount": 2.5, "net_mf_amount": -0.25},
        {"buy_sm_vol": 1300, "sell_elg_vol": 200, "net_mf_vol": -300,
         "buy_sm_amount": 12500, "sell_elg_amount": 25000, "net_mf_amount": -2500}),
    ("top_list", {"amount": 12.3, "net_rate": 2.5}, {"amount": 12.3, "net_rate": 2.5}),
    ("margin", {"rqyl": 12.3, "rzye": 123.4}, {"rqyl": 12.3, "rzye": 123.4}),
    ("cyq_perf", {"cost_5pct": 10.123, "winner_rate": 3.52}, {"cost_5pct": 10.123, "winner_rate": 3.52}),
])
def test_source_specific_units_preserve_ratios_prices_and_unconfirmed_fields(
    tmp_path: Path, target: str, fields: dict[str, float | None], expected: dict[str, float | None],
) -> None:
    raw = {"ts_code": ["000001.SZ"], "trade_date": ["20260720"],
           **{column: [value] for column, value in fields.items()}}
    if target == "stock_basic":
        raw["list_date"] = ["19910403"]
    path = tmp_path / "raw.parquet"
    pd.DataFrame(raw).to_parquet(path, index=False)
    table = normalize_tushare(input_file=path, output_name=tmp_path / "processed.parquet",
        raw_object=target, target_name=target, trade_date="2026-07-20").table
    for column, value in expected.items():
        actual = table[column][0].as_py()
        if value is None:
            assert actual is None
        else:
            assert actual == pytest.approx(value)
    if target == "moneyflow":
        assert table.schema.field("net_mf_vol").type == pa.int64()
