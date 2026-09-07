# filepath: tests/data_system/steps/test_stock_1430_build.py
"""Behavior tests for H03 Feature/Label materialization."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src import logs
from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps import stock_1430_build as step_module
from src.data_system.steps.stock_1430_build import Stock1430BuildStep
from src.utils.path import PathManager


@pytest.mark.parametrize("all_null_label", (False, True))
def test_stock_1430_step_publishes_pair_and_reuses_meta_without_input_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    all_null_label: bool,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    feature_output = pa.table(
        {
            "symbol": ["000001"],
            "trade_date": [trade_date],
            "decision_ts_utc": pa.array([1], type=pa.int64()),
            "feature": [0.5],
        }
    )
    label_output = pa.table(
        {
            "symbol": ["000001"],
            "trade_date": [trade_date],
            "decision_ts_utc": pa.array([1], type=pa.int64()),
            "y_rank_return": pa.array(
                [None if all_null_label else 1.0], type=pa.float64()
            ),
        }
    )
    feature_builder = Mock(return_value=feature_output)
    label_builder = Mock(return_value=label_output)
    monkeypatch.setattr(step_module, "build_stock_1430_features", feature_builder)
    monkeypatch.setattr(step_module, "build_stock_1430_labels", label_builder)
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)
    access = Mock(spec=Access)
    access.next_trade_date.return_value = next_trade_date
    access.stock_trade_minutes.side_effect = [
        pa.table({"source": ["feature"]}),
        pa.table({"source": ["entry"]}),
        pa.table({"source": ["exit"]}),
    ]
    access.adjustment_factors.side_effect = [
        _factors(trade_date),
        _factors(next_trade_date),
    ]
    step = Stock1430BuildStep(pm=pm, access=access)
    context = DataContext(
        start=trade_date,
        end=trade_date,
        trade_dates=(trade_date,),
    )
    parquet_file = pq.ParquetFile
    opened_readers: list[pq.ParquetFile] = []

    def _track_parquet_open(path: Path) -> pq.ParquetFile:
        reader = parquet_file(path)
        opened_readers.append(reader)
        return reader

    monkeypatch.setattr(step_module.pq, "ParquetFile", _track_parquet_open)

    assert step.run(context) is context
    assert opened_readers
    assert all(reader.closed for reader in opened_readers)

    feature_paths = pm.feature_object(
        feature_set="l2_stock_1430",
        version="v1",
        trade_date=trade_date,
    )
    label_paths = pm.label_object(
        label_set="l2_stock_1430_t1_vwap_rank",
        version="v1",
        trade_date=trade_date,
    )
    assert pq.read_table(feature_paths.payload_path).equals(feature_output)
    assert pq.read_table(label_paths.payload_path).equals(label_output)
    assert set(json.loads(feature_paths.meta_path.read_text())) == {
        "payload",
        "size_bytes",
    }
    assert set(json.loads(label_paths.meta_path.read_text())) == {
        "payload",
        "size_bytes",
    }
    assert access.stock_trade_minutes.call_count == 3
    access.next_trade_date.assert_called_once_with(trade_date=trade_date)
    assert feature_builder.call_args.kwargs["trade_date"] == date(2026, 5, 6)
    assert label_builder.call_args.kwargs["trade_date"] == date(2026, 5, 6)
    assert label_builder.call_args.kwargs["next_trade_date"] == date(2026, 5, 7)
    assert label_builder.call_args.kwargs["feature_keys"].equals(
        feature_output.select(["symbol", "trade_date", "decision_ts_utc"])
    )

    feature_builder.side_effect = AssertionError("Feature builder must not run")
    label_builder.side_effect = AssertionError("Label builder must not run")
    access.reset_mock()
    access.stock_trade_minutes.side_effect = AssertionError("minutes must not be read")
    access.adjustment_factors.side_effect = AssertionError("factors must not be read")
    access.next_trade_date.side_effect = AssertionError("calendar must not be read")
    monkeypatch.setattr(
        step_module.pq,
        "ParquetFile",
        Mock(side_effect=AssertionError("Feature payload must not be read")),
    )

    assert step.run(context) is context
    access.stock_trade_minutes.assert_not_called()
    access.adjustment_factors.assert_not_called()
    access.next_trade_date.assert_not_called()
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "✅ stock 14:30 Feature; trade_date=2026-05-06 version=v1 rows=1",
        "✅ stock 14:30 Label; trade_date=2026-05-06 version=v1 rows=1",
        "♻️ stock 14:30 Feature; trade_date=2026-05-06 version=v1",
        "♻️ stock 14:30 Label; trade_date=2026-05-06 version=v1",
    ]


def test_stock_1430_step_keeps_feature_and_resumes_label_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    feature_output = pa.table(
        {
            "symbol": ["000001"],
            "trade_date": [trade_date],
            "decision_ts_utc": pa.array([1], type=pa.int64()),
            "feature": [1.0],
        }
    )
    label_output = pa.table({"label": [1.0]})
    feature_builder = Mock(return_value=feature_output)
    label_builder = Mock(side_effect=[RuntimeError("label failure"), label_output])
    monkeypatch.setattr(step_module, "build_stock_1430_features", feature_builder)
    monkeypatch.setattr(step_module, "build_stock_1430_labels", label_builder)
    access = Mock(spec=Access)
    access.next_trade_date.return_value = "2026-05-07"
    access.stock_trade_minutes.return_value = pa.table({"source": [1]})
    access.adjustment_factors.return_value = _factors(trade_date)
    step = Stock1430BuildStep(pm=pm, access=access)
    context = DataContext(
        start=trade_date,
        end=trade_date,
        trade_dates=(trade_date,),
    )

    with pytest.raises(RuntimeError, match="label failure"):
        step.run(context)

    feature_paths = pm.feature_object(
        feature_set="l2_stock_1430",
        version="v1",
        trade_date=trade_date,
    )
    label_paths = pm.label_object(
        label_set="l2_stock_1430_t1_vwap_rank",
        version="v1",
        trade_date=trade_date,
    )
    assert feature_paths.meta_path.is_file()
    assert not label_paths.meta_path.exists()

    assert step.run(context) is context
    assert feature_builder.call_count == 1
    assert label_builder.call_count == 2
    assert label_paths.meta_path.is_file()


def test_stock_1430_step_requires_feature_meta_before_label_input_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    feature_paths = pm.feature_object(
        feature_set="l2_stock_1430", version="v1", trade_date=trade_date
    )
    feature_paths.payload_path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"feature": [1.0]}), feature_paths.payload_path)
    meta.commit(pm=pm, payload_path=feature_paths.payload_path)
    failure = FileNotFoundError("required Feature Meta is unavailable")
    monkeypatch.setattr(meta, "require", Mock(side_effect=failure))
    label_builder = Mock(return_value=pa.table({"label": [1.0]}))
    monkeypatch.setattr(step_module, "build_stock_1430_labels", label_builder)
    access = Mock(spec=Access)
    access.next_trade_date.return_value = "2026-05-07"

    with pytest.raises(FileNotFoundError) as caught:
        Stock1430BuildStep(pm=pm, access=access).run(
            DataContext(start=trade_date, end=trade_date, trade_dates=(trade_date,))
        )

    assert caught.value is failure
    assert not list(pm.storage_root.glob("labels/**/*.parquet"))
    access.next_trade_date.assert_not_called()
    access.stock_trade_minutes.assert_not_called()
    access.adjustment_factors.assert_not_called()
    label_builder.assert_not_called()


def test_stock_1430_step_rejects_relationship_meta_without_rebuilding(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    upstream_paths = pm.processed_object(
        dataset_name="dummy",
        version="v1",
        trade_date=trade_date,
    )
    upstream_paths.payload_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"value": [1]}), upstream_paths.payload_path)
    meta.commit(pm=pm, payload_path=upstream_paths.payload_path)
    feature_paths = pm.feature_object(
        feature_set="l2_stock_1430",
        version="v1",
        trade_date=trade_date,
    )
    feature_paths.payload_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"feature": [1.0]}), feature_paths.payload_path)
    meta.commit(
        pm=pm,
        payload_path=feature_paths.payload_path,
        upstream_meta_path=upstream_paths.meta_path,
    )
    access = Mock(spec=Access)

    with pytest.raises(RuntimeError, match="must not contain upstream"):
        Stock1430BuildStep(pm=pm, access=access).run(
            DataContext(
                start=trade_date,
                end=trade_date,
                trade_dates=(trade_date,),
            )
        )

    access.stock_trade_minutes.assert_not_called()


def test_stock_1430_step_rejects_empty_feature_before_label_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    access = Mock(spec=Access)
    access.stock_trade_minutes.return_value = pa.table({"source": []})
    monkeypatch.setattr(
        step_module, "build_stock_1430_features", Mock(return_value=pa.table({"f": []}))
    )

    with pytest.raises(ValueError, match="at least one row"):
        Stock1430BuildStep(pm=pm, access=access).run(
            DataContext(start=trade_date, end=trade_date, trade_dates=(trade_date,))
        )

    assert not list(pm.storage_root.glob("features/**/*.parquet"))
    assert not list(pm.storage_root.glob("labels/**/*.parquet"))
    access.next_trade_date.assert_not_called()
    access.adjustment_factors.assert_not_called()


@pytest.mark.parametrize("invalid_object", ("feature", "label"))
@pytest.mark.parametrize("invalid_meta", ("size", "symbol_slices"))
def test_stock_1430_step_preserves_invalid_meta_and_fails_without_input_reads(
    tmp_path: Path,
    invalid_object: str,
    invalid_meta: str,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    feature_paths = pm.feature_object(
        feature_set="l2_stock_1430", version="v1", trade_date=trade_date
    )
    label_paths = pm.label_object(
        label_set="l2_stock_1430_t1_vwap_rank", version="v1", trade_date=trade_date
    )
    for paths in (feature_paths, label_paths):
        paths.payload_path.parent.mkdir(parents=True)
        pq.write_table(pa.table({"value": [1.0]}), paths.payload_path)
        meta.commit(pm=pm, payload_path=paths.payload_path)
    invalid_paths = feature_paths if invalid_object == "feature" else label_paths
    document = json.loads(invalid_paths.meta_path.read_text())
    if invalid_meta == "size":
        document["size_bytes"] += 1
    else:
        document["symbol_slices"] = {"000001": {"start": 0, "end": 1}}
    invalid_paths.meta_path.write_text(json.dumps(document))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for paths in (feature_paths, label_paths)
        for path in (paths.payload_path, paths.meta_path)
    }
    access = Mock(spec=Access)

    with pytest.raises(RuntimeError, match="size|symbol_slices"):
        Stock1430BuildStep(pm=pm, access=access).run(
            DataContext(start=trade_date, end=trade_date, trade_dates=(trade_date,))
        )

    for path, snapshot in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == snapshot
    access.stock_trade_minutes.assert_not_called()
    access.adjustment_factors.assert_not_called()
    access.next_trade_date.assert_not_called()


def _factors(trade_date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001"],
            "trade_date": [trade_date],
            "adj_factor": [1.0],
        }
    )
