# filepath: tests/data_system/steps/test_level2_minute_build.py
"""Publication tests for the Level2 stock minute build Step."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps import level2_minute_build as step_module
from src.data_system.steps.level2_minute_build import Level2MinuteBuildStep
from src.utils.path import PathManager

_TRADE_DATE = "2026-05-06"
_MINUTE_US = 60_000_000


def test_step_builds_both_markets_with_direct_lineage_and_no_symbol_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    _write_level2_input(
        pm,
        dataset_name="sh_trade",
        symbols=("510050", "600000", "600001"),
        security_types=("etf", "stock", "stock"),
    )
    _write_level2_input(
        pm,
        dataset_name="sz_trade",
        symbols=("000001",),
        security_types=("stock",),
    )
    logger = Mock()
    monkeypatch.setattr(step_module, "logs", logger)
    step = Level2MinuteBuildStep(
        pm=pm,
        access=Access(pm=pm, processed_version="v1"),
        processed_version="v1",
        symbol_batch_size=1,
    )
    context = DataContext(
        start=_TRADE_DATE,
        end=_TRADE_DATE,
        trade_dates=(_TRADE_DATE,),
    )

    assert step.run(context) is context

    sh_output, sh_record = _read_minute_output(pm, "sh_stock_trade_1m")
    sz_output, sz_record = _read_minute_output(pm, "sz_stock_trade_1m")
    sh_input_paths = pm.processed_object(
        dataset_name="sh_trade",
        version="v1",
        trade_date=_TRADE_DATE,
    )
    sz_input_paths = pm.processed_object(
        dataset_name="sz_trade",
        version="v1",
        trade_date=_TRADE_DATE,
    )
    assert sh_output.column("symbol").to_pylist() == ["600000", "600001"]
    assert sz_output.column("symbol").to_pylist() == ["000001"]
    assert sh_record.symbol_slices is None
    assert sz_record.symbol_slices is None
    assert sh_record.upstream == (
        PurePosixPath(
            f"processed/sh_trade/v1/trade_date={_TRADE_DATE}/meta.json"
        ),
        meta.require(
            pm=pm,
            meta_path=sh_input_paths.meta_path,
        ).size_bytes,
    )
    assert sz_record.upstream == (
        PurePosixPath(
            f"processed/sz_trade/v1/trade_date={_TRADE_DATE}/meta.json"
        ),
        meta.require(
            pm=pm,
            meta_path=sz_input_paths.meta_path,
        ).size_bytes,
    )
    starts = [
        call.args[0]
        for call in logger.info.call_args_list
        if call.args[0].startswith("▶️")
    ]
    assert starts == [
        "▶️ Level-2 minute fact; target=sh_stock_trade_1m "
        f"trade_date={_TRADE_DATE} symbols=3",
        "▶️ Level-2 minute fact; target=sz_stock_trade_1m "
        f"trade_date={_TRADE_DATE} symbols=1",
    ]


def test_step_publishes_typed_empty_objects_for_valid_non_stock_inputs(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    _write_level2_input(
        pm,
        dataset_name="sh_trade",
        symbols=("510050",),
        security_types=("etf",),
    )
    _write_level2_input(
        pm,
        dataset_name="sz_trade",
        symbols=("110000",),
        security_types=("bond",),
    )

    Level2MinuteBuildStep(
        pm=pm,
        access=Access(pm=pm, processed_version="v1"),
        processed_version="v1",
        symbol_batch_size=64,
    ).run(
        DataContext(
            start=_TRADE_DATE,
            end=_TRADE_DATE,
            trade_dates=(_TRADE_DATE,),
        )
    )

    for dataset_name in ("sh_stock_trade_1m", "sz_stock_trade_1m"):
        output, record = _read_minute_output(pm, dataset_name)
        assert output.num_rows == 0
        assert output.column_names == [
            "symbol",
            "trade_date",
            "minute_start_ts_utc",
            "phase",
            "open",
            "high",
            "low",
            "close",
            "volume_sum",
            "notional_sum",
            "trade_count",
            "tick_signed_volume_sum",
            "tick_signed_notional_sum",
        ]
        assert record.upstream is not None


def test_step_keeps_sh_publish_when_sz_is_missing_and_resumes_on_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    _write_level2_input(
        pm,
        dataset_name="sh_trade",
        symbols=("600000",),
        security_types=("stock",),
    )
    context = DataContext(
        start=_TRADE_DATE,
        end=_TRADE_DATE,
        trade_dates=(_TRADE_DATE,),
    )
    step = Level2MinuteBuildStep(
        pm=pm,
        access=Access(pm=pm, processed_version="v1"),
        processed_version="v1",
        symbol_batch_size=64,
    )

    with pytest.raises(FileNotFoundError, match="required Meta"):
        step.run(context)

    sh_meta_path = pm.processed_object(
        dataset_name="sh_stock_trade_1m",
        version="v1",
        trade_date=_TRADE_DATE,
    ).meta_path
    assert sh_meta_path.is_file()
    _write_level2_input(
        pm,
        dataset_name="sz_trade",
        symbols=("000001",),
        security_types=("stock",),
    )
    logger = Mock()
    monkeypatch.setattr(step_module, "logs", logger)

    step.run(context)

    assert pm.processed_object(
        dataset_name="sz_stock_trade_1m",
        version="v1",
        trade_date=_TRADE_DATE,
    ).meta_path.is_file()
    assert logger.info.call_args_list[0].args[0].startswith(
        "♻️ Level-2 minute fact; target=sh_stock_trade_1m"
    )


def test_step_reuses_both_valid_outputs_without_access_reads(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    for dataset_name, symbol in (
        ("sh_trade", "600000"),
        ("sz_trade", "000001"),
    ):
        _write_level2_input(
            pm,
            dataset_name=dataset_name,
            symbols=(symbol,),
            security_types=("stock",),
        )
    context = DataContext(
        start=_TRADE_DATE,
        end=_TRADE_DATE,
        trade_dates=(_TRADE_DATE,),
    )
    Level2MinuteBuildStep(
        pm=pm,
        access=Access(pm=pm, processed_version="v1"),
        processed_version="v1",
        symbol_batch_size=64,
    ).run(context)
    payloads = [
        pm.processed_object(
            dataset_name=dataset_name,
            version="v1",
            trade_date=_TRADE_DATE,
        ).payload_path
        for dataset_name in ("sh_stock_trade_1m", "sz_stock_trade_1m")
    ]
    mtimes = [path.stat().st_mtime_ns for path in payloads]
    access = Mock(spec=Access)

    Level2MinuteBuildStep(
        pm=pm,
        access=access,
        processed_version="v1",
        symbol_batch_size=64,
    ).run(context)

    access.level2_symbols.assert_not_called()
    access.trades.assert_not_called()
    assert [path.stat().st_mtime_ns for path in payloads] == mtimes


def test_step_rejects_existing_output_without_the_exact_upstream(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    _write_level2_input(
        pm,
        dataset_name="sh_trade",
        symbols=("600000",),
        security_types=("stock",),
    )
    output_path = pm.processed_object(
        dataset_name="sh_stock_trade_1m",
        version="v1",
        trade_date=_TRADE_DATE,
    ).payload_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"unexpected": [1]}), output_path)
    meta.commit(pm=pm, payload_path=output_path)

    with pytest.raises(RuntimeError, match="upstream mismatch"):
        Level2MinuteBuildStep(
            pm=pm,
            access=Access(pm=pm, processed_version="v1"),
            processed_version="v1",
            symbol_batch_size=64,
        ).run(
            DataContext(
                start=_TRADE_DATE,
                end=_TRADE_DATE,
                trade_dates=(_TRADE_DATE,),
            )
        )


def test_step_rate_limits_progress_after_completed_symbol_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    _write_level2_input(
        pm,
        dataset_name="sh_trade",
        symbols=("600000", "600001", "600002"),
        security_types=("stock", "stock", "stock"),
    )
    _write_level2_input(
        pm,
        dataset_name="sz_trade",
        symbols=("000001",),
        security_types=("stock",),
    )
    logger = Mock()
    monkeypatch.setattr(step_module, "logs", logger)
    monotonic_values = iter([0.0, 31.0, 40.0, 61.0, 62.0, 70.0, 71.0, 72.0])
    monkeypatch.setattr(step_module, "monotonic", lambda: next(monotonic_values))

    Level2MinuteBuildStep(
        pm=pm,
        access=Access(pm=pm, processed_version="v1"),
        processed_version="v1",
        symbol_batch_size=1,
    ).run(
        DataContext(
            start=_TRADE_DATE,
            end=_TRADE_DATE,
            trade_dates=(_TRADE_DATE,),
        )
    )

    progress = [
        call.args[0]
        for call in logger.info.call_args_list
        if call.args[0].startswith("⏳")
    ]
    assert progress == [
        "⏳ Level-2 minute fact; target=sh_stock_trade_1m "
        f"trade_date={_TRADE_DATE} symbols_processed=1 symbols=3 "
        "elapsed_seconds=31"
    ]


@pytest.mark.parametrize("symbol_batch_size", [True, 0])
def test_step_rejects_invalid_symbol_batch_size(
    tmp_path: Path,
    symbol_batch_size: int,
) -> None:
    error_type = TypeError if symbol_batch_size is True else ValueError
    with pytest.raises(error_type, match="symbol_batch_size"):
        Level2MinuteBuildStep(
            pm=PathManager(tmp_path),
            access=Mock(spec=Access),
            processed_version="v1",
            symbol_batch_size=symbol_batch_size,
        )


def _write_level2_input(
    pm: PathManager,
    *,
    dataset_name: str,
    symbols: tuple[str, ...],
    security_types: tuple[str, ...],
) -> None:
    rows = len(symbols)
    table = pa.table(
        {
            "symbol": pa.array(symbols, type=pa.string()),
            "ts_utc": pa.array(
                [_MINUTE_US + index for index in range(rows)],
                type=pa.int64(),
            ),
            "main_seq": pa.array([1] * rows, type=pa.int64()),
            "sub_seq": pa.array(
                list(range(1, rows + 1)),
                type=pa.int64(),
            ),
            "price": pa.array(
                [10.0 + index for index in range(rows)],
                type=pa.float64(),
            ),
            "volume": pa.array([100] * rows, type=pa.int64()),
            "security_type": pa.array(security_types, type=pa.string()),
            "phase": pa.array([2] * rows, type=pa.int8()),
            "notional": pa.array(
                [1_000.0 + 100.0 * index for index in range(rows)],
                type=pa.float64(),
            ),
            "trade_side": pa.array([0] * rows, type=pa.int8()),
        }
    )
    output_path = pm.processed_object(
        dataset_name=dataset_name,
        version="v1",
        trade_date=_TRADE_DATE,
    ).payload_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path, row_group_size=1)
    meta.commit(
        pm=pm,
        payload_path=output_path,
        symbol_slices={
            symbol: range(index, index + 1)
            for index, symbol in enumerate(symbols)
        },
    )


def _read_minute_output(
    pm: PathManager,
    dataset_name: str,
) -> tuple[pa.Table, meta.MetaRecord]:
    output_paths = pm.processed_object(
        dataset_name=dataset_name,
        version="v1",
        trade_date=_TRADE_DATE,
    )
    record = meta.require(
        pm=pm,
        meta_path=output_paths.meta_path,
        expected_payload_path=output_paths.payload_path,
    )
    return pq.ParquetFile(record.payload_path).read(), record
