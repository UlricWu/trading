# filepath: tests/data_system/steps/test__partition.py
"""Behavior tests for the shared partition publication boundary."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src import logs
from src.access import meta
from src.data_system.steps import _partition as partition_module
from src.data_system.steps._partition import (
    _publish_parquet_object,
    _publish_partition,
)
from src.utils.path import PathManager


@pytest.mark.parametrize(
    ("dataset_name", "symbols", "symbol_slices"),
    (
        ("stock_st", [], None),
        (
            "sh_trade",
            ["600000", "600000", "600001"],
            MappingProxyType({"600000": range(2), "600001": range(2, 3)}),
        ),
    ),
)
def test_parquet_publication_preserves_empty_tables_and_symbol_slices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dataset_name: str,
    symbols: list[str],
    symbol_slices: Mapping[str, range] | None,
) -> None:
    pm = PathManager(tmp_path)
    raw_payload = pm.raw_payload(
        broker="broker",
        source_name=dataset_name,
        trade_date="2026-01-02",
        payload_file="source.csv.7z",
    )
    raw_payload.parent.mkdir(parents=True)
    raw_payload.write_bytes(b"source payload")
    meta.commit(pm=pm, payload_path=raw_payload)
    upstream_meta_path = pm.raw_meta(
        broker="broker", source_name=dataset_name, trade_date="2026-01-02"
    )
    paths = pm.processed_object(
        dataset_name=dataset_name, version="v1", trade_date="2026-01-02"
    )
    table = pa.table({"symbol": pa.array(symbols, type=pa.string())})
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)

    _publish_parquet_object(
        pm=pm,
        paths=paths,
        table=table,
        upstream_meta_path=upstream_meta_path,
        symbol_slices=symbol_slices,
    )

    record = meta.require(
        pm=pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path
    )
    with pq.ParquetFile(record.payload_path) as parquet_file:
        assert parquet_file.read().equals(table)
    assert record.symbol_slices == symbol_slices
    assert record.upstream is not None
    assert record.upstream[0].as_posix() == (
        upstream_meta_path.relative_to(pm.storage_root).as_posix()
    )
    assert record.upstream[1] == raw_payload.stat().st_size
    logger.info.assert_not_called()


@pytest.mark.parametrize("has_upstream", (False, True))
def test_partition_publishes_and_preserves_payload_and_meta_on_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_upstream: bool,
) -> None:
    pm = PathManager(tmp_path)
    table = pa.table({"value": [1, 2]})
    upstream_meta_path = None
    if has_upstream:
        paths = pm.processed_year_object(
            dataset_name="trade_calendar", version="v1", calendar_year=2026
        )
        raw_payload = pm.raw_year_payload(
            broker="tushare",
            source_name="trade_calendar",
            calendar_year=2026,
            payload_file="data.parquet",
        )
        raw_payload.parent.mkdir(parents=True)
        pq.write_table(table, raw_payload)
        meta.commit(pm=pm, payload_path=raw_payload)
        upstream_meta_path = raw_payload.parent / "meta.json"
    else:
        paths = pm.feature_object(
            feature_set="daily", version="v1", trade_date="2026-01-02"
        )
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)

    builder = Mock(return_value=table)
    rows = _publish_partition(
        pm=pm,
        paths=paths,
        build=builder,
        who="partition; case=publication",
        upstream_meta_path=upstream_meta_path,
    )
    assert rows == 2
    builder.assert_called_once_with()

    with pq.ParquetFile(paths.payload_path) as parquet_file:
        assert parquet_file.read().equals(table)
    record = meta.require(pm=pm, meta_path=paths.meta_path)
    assert record.symbol_slices is None
    if upstream_meta_path is None:
        assert record.upstream is None
    else:
        assert record.upstream is not None
        assert record.upstream[0].as_posix() == (
            upstream_meta_path.relative_to(pm.storage_root).as_posix()
        )
    payload_before = paths.payload_path.read_bytes()
    meta_before = paths.meta_path.read_bytes()

    builder.side_effect = AssertionError("reuse must not build")
    assert (
        _publish_partition(
            pm=pm,
            paths=paths,
            build=builder,
            who="partition; case=publication",
            upstream_meta_path=upstream_meta_path,
        )
        is None
    )

    assert paths.payload_path.read_bytes() == payload_before
    assert paths.meta_path.read_bytes() == meta_before
    builder.assert_called_once_with()
    logger.info.assert_not_called()


@pytest.mark.parametrize("failure_stage", ("build", "empty", "payload", "meta"))
def test_partition_failure_never_commits_meta_or_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    pm = PathManager(tmp_path)
    paths = pm.feature_object(
        feature_set="daily", version="v1", trade_date="2026-01-02"
    )
    table = pa.table({"value": [1]})
    failure = RuntimeError("publication failed")
    if failure_stage == "empty":
        table = pa.table({"value": pa.array([], type=pa.int64())})
    elif failure_stage == "payload":
        monkeypatch.setattr(
            partition_module, "write_parquet_atomic", Mock(side_effect=failure)
        )
    elif failure_stage == "meta":
        monkeypatch.setattr(meta, "commit", Mock(side_effect=failure))
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)
    exception_type = ValueError if failure_stage == "empty" else RuntimeError

    with pytest.raises(exception_type) as caught:
        _publish_partition(
            pm=pm,
            paths=paths,
            build=Mock(side_effect=failure)
            if failure_stage == "build"
            else lambda: table,
            who="partition; case=failure",
        )

    if failure_stage == "empty":
        assert "at least one row" in str(caught.value)
    else:
        assert caught.value is failure
    assert not paths.meta_path.exists()
    assert paths.payload_path.exists() is (failure_stage == "meta")
    logger.info.assert_not_called()
