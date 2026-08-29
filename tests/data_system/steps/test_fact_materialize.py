# filepath: tests/data_system/steps/test_fact_materialize.py
"""Behavior tests for range fact materialization."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter, DownloadPlan
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOutput
from src.data_system.steps import fact_materialize as fact_module
from src.data_system.steps.fact_materialize import FactMaterializeStep
from src.utils.path import PathManager


def _source(raw_object: str, *, outputs: list[str] | None = None) -> SourceConfig:
    return SourceConfig(
        enabled=True,
        broker="broker",
        group="offline_standard",
        raw_object=raw_object,
        outputs=outputs if outputs is not None else [raw_object],
    )


def _context(*trade_dates: str) -> DataContext:
    return DataContext(
        start=trade_dates[0],
        end=trade_dates[-1],
        trade_dates=trade_dates,
    )


def test_fact_step_attempts_every_source_then_rejects_partial_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.side_effect = [object(), object()]
    path_manager.raw_payload.return_value = object()
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = [
        None,
        DownloadPlan(
            source_name="available",
            trade_date="2026-07-20",
            broker="broker",
            raw_object="available",
        ),
    ]
    adapter_class = Mock(return_value=adapter)
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=None))
    commit = Mock()
    monkeypatch.setattr(fact_module.meta, "commit", commit)
    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={
            "missing": _source("missing"),
            "available": _source("available"),
        },
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_operations={"broker": Mock()},
        processed_version="v1",
        adapter_cache={},
    )

    with pytest.raises(RuntimeError, match="only partially available"):
        step.run(_context("2026-07-20"))

    assert adapter.fetch.call_count == 2
    adapter_class.assert_called_once()
    commit.assert_called_once()


def test_fact_step_reports_all_wholly_missing_dates_without_normalizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.return_value = None
    adapter_class = Mock(return_value=adapter)
    normalize = Mock()
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=None))
    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"bars": _source("bars")},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_operations={"broker": normalize},
        processed_version="v1",
        adapter_cache={},
    )

    with pytest.raises(
        RuntimeError,
        match=r"missing_dates=\['2026-07-20', '2026-07-21'\]",
    ):
        step.run(_context("2026-07-20", "2026-07-21"))

    assert [
        call.kwargs["record"].trade_date for call in adapter.fetch.call_args_list
    ] == ["2026-07-20", "2026-07-21"]
    adapter_class.assert_called_once()
    normalize.assert_not_called()


def test_fact_step_raw_meta_hit_does_not_construct_a_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(fact_module, "logs", logger)
    monkeypatch.setattr(
        fact_module.time,
        "perf_counter",
        Mock(side_effect=[1.0, 1.25]),
    )
    path_manager = Mock(spec=PathManager)
    raw_meta_path = Path("/raw/bars/meta.json")
    path_manager.raw_meta.return_value = raw_meta_path
    adapter_class = Mock()
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=object()))
    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"bars": _source("bars", outputs=[])},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_operations={},
        processed_version="v1",
        adapter_cache={},
    )
    context = _context("2026-07-20")

    assert step.run(context) is context
    adapter_class.assert_not_called()
    assert [call.args[0] for call in logger.info.call_args_list] == [
        f"♻️ raw meta hit; source=bars broker=broker trade_date=2026-07-20 "
        f"meta={raw_meta_path}",
        "✅ fact date; trade_date=2026-07-20 elapsed_seconds=0.250",
        "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
        "processed_reused=0 processed_published=0 unavailable=0",
    ]


def test_fact_step_uses_matching_staging_payload_for_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(fact_module, "logs", logger)
    monkeypatch.setattr(
        fact_module.time,
        "perf_counter",
        Mock(side_effect=[1.0, 2.0, 2.5, 3.0, 4.0, 4.1]),
    )
    path_manager = PathManager(tmp_path)
    trade_date = "2026-05-01"
    raw_path = path_manager.raw_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file="source.csv.7z",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"same-size")
    meta.commit(pm=path_manager, payload_path=raw_path)
    staging_path = path_manager.staging_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file=raw_path.name,
    )
    staging_path.parent.mkdir(parents=True)
    staging_path.write_bytes(b"same-size")
    selected_inputs: list[Path] = []

    def normalize_operation(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        selected_inputs.append(input_file)
        return NormalizeOutput(table=pa.table({"value": [1]}))

    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"source": _source("raw_object", outputs=["output"])},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock()},
        ),
        normalize_operations={"broker": normalize_operation},
        processed_version="v1",
        adapter_cache={},
    )

    step.run(_context(trade_date))
    step.run(_context(trade_date))

    assert selected_inputs == [staging_path]
    output_path = path_manager.processed_data(
        dataset_name="output",
        version="v1",
        trade_date=trade_date,
    )
    raw_meta_path = path_manager.raw_meta(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
    )
    processed_meta_path = path_manager.processed_meta(
        dataset_name="output",
        version="v1",
        trade_date=trade_date,
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        f"♻️ raw meta hit; source=source broker=broker trade_date={trade_date} "
        f"meta={raw_meta_path}",
        f"✅ processed publish; target=output source=source "
        f"trade_date={trade_date} rows=1 normalize_seconds=0.500 "
        f"output={output_path}",
        f"✅ fact date; trade_date={trade_date} elapsed_seconds=2.000",
        "\n".join(
            (
                "✅ ===== Fact operation summary =====",
                f"{'normalize output':<35} {0.5:>8.3f}s "
                "avg=0.500s runs=1",
            )
        ),
        "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
        "processed_reused=0 processed_published=1 unavailable=0",
        f"♻️ raw meta hit; source=source broker=broker trade_date={trade_date} "
        f"meta={raw_meta_path}",
        f"♻️ processed meta hit; target=output source=source "
        f"trade_date={trade_date} meta={processed_meta_path}",
        f"✅ fact date; trade_date={trade_date} elapsed_seconds=0.100",
        "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
        "processed_reused=1 processed_published=0 unavailable=0",
    ]


def test_fact_step_times_each_real_ingest_and_normalize_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(fact_module, "logs", logger)
    monkeypatch.setattr(
        fact_module.time,
        "perf_counter",
        Mock(
            side_effect=[
                0.0,
                1.0,
                3.0,
                4.0,
                7.0,
                8.0,
                10.0,
                11.0,
                16.0,
                17.0,
                21.0,
                22.0,
            ]
        ),
    )
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.side_effect = lambda **kwargs: Path(
        f"/raw/{kwargs['trade_date']}/meta.json"
    )
    path_manager.raw_payload.side_effect = lambda **kwargs: Path(
        f"/raw/{kwargs['trade_date']}/{kwargs['payload_file']}"
    )
    path_manager.processed_meta.side_effect = lambda **kwargs: Path(
        f"/processed/{kwargs['trade_date']}/meta.json"
    )
    path_manager.processed_data.side_effect = lambda **kwargs: Path(
        f"/processed/{kwargs['trade_date']}/data.parquet"
    )
    path_manager.staging_payload.side_effect = lambda **kwargs: Path(
        f"/staging/{kwargs['trade_date']}/{kwargs['payload_file']}"
    )
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=None))
    monkeypatch.setattr(
        fact_module.meta,
        "require",
        Mock(
            side_effect=[
                Mock(
                    payload_path=Path("/raw/2026-07-20/data.parquet"),
                    size_bytes=1,
                ),
                Mock(
                    payload_path=Path("/raw/2026-07-21/data.parquet"),
                    size_bytes=1,
                ),
            ]
        ),
    )
    monkeypatch.setattr(fact_module.meta, "commit", Mock())
    monkeypatch.setattr(fact_module, "write_parquet_atomic", Mock())
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = lambda *, record, pm: DownloadPlan(
        source_name=record.source_name,
        trade_date=record.trade_date,
        broker=record.broker,
        raw_object=record.raw_object,
        payload_file="data.parquet",
    )
    normalize = Mock(return_value=NormalizeOutput(table=pa.table({"value": [1]})))
    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"source": _source("raw_object", outputs=["output"])},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock()},
        ),
        normalize_operations={"broker": normalize},
        processed_version="v1",
        adapter_cache={"broker": adapter},
    )

    result = step.run(_context("2026-07-20", "2026-07-21"))

    assert result.trade_dates == ("2026-07-20", "2026-07-21")
    assert adapter.fetch.call_count == 2
    assert normalize.call_count == 2
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "✅ raw ingest; source=source broker=broker trade_date=2026-07-20 "
        "elapsed_seconds=2.000 output=/raw/2026-07-20/data.parquet",
        "✅ processed publish; target=output source=source "
        "trade_date=2026-07-20 rows=1 normalize_seconds=3.000 "
        "output=/processed/2026-07-20/data.parquet",
        "✅ fact date; trade_date=2026-07-20 elapsed_seconds=8.000",
        "✅ raw ingest; source=source broker=broker trade_date=2026-07-21 "
        "elapsed_seconds=5.000 output=/raw/2026-07-21/data.parquet",
        "✅ processed publish; target=output source=source "
        "trade_date=2026-07-21 rows=1 normalize_seconds=4.000 "
        "output=/processed/2026-07-21/data.parquet",
        "✅ fact date; trade_date=2026-07-21 elapsed_seconds=12.000",
        "\n".join(
            (
                "✅ ===== Fact operation summary =====",
                f"{'raw ingest source':<35} {7.0:>8.3f}s "
                "avg=3.500s runs=2",
                f"{'normalize output':<35} {7.0:>8.3f}s "
                "avg=3.500s runs=2",
            )
        ),
        "✅ fact materialize; trade_dates=2 raw_reused=0 raw_fetched=2 "
        "processed_reused=0 processed_published=2 unavailable=0",
    ]


@pytest.mark.parametrize(
    "empty_source",
    ("stock_basic", "stock_st", "suspend_d"),
)
def test_fact_step_publishes_allowed_empty_outputs(
    tmp_path: Path,
    empty_source: str,
) -> None:
    path_manager = PathManager(tmp_path)
    trade_date = "2019-04-01"
    raw_path = path_manager.raw_payload(
        broker="broker",
        source_name=empty_source,
        trade_date=trade_date,
        payload_file="data.parquet",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"empty-source")
    meta.commit(pm=path_manager, payload_path=raw_path)

    def normalize_operation(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        return NormalizeOutput(
            table=pa.table(
                {"symbol": pa.array([], type=pa.string())}
            )
        )

    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={empty_source: _source(empty_source)},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock()},
        ),
        normalize_operations={"broker": normalize_operation},
        processed_version="v1",
        adapter_cache={},
    )

    context = step.run(_context(trade_date))

    processed_path = path_manager.processed_data(
        dataset_name=empty_source,
        version="v1",
        trade_date=trade_date,
    )
    processed_meta = path_manager.processed_meta(
        dataset_name=empty_source,
        version="v1",
        trade_date=trade_date,
    )
    loaded = meta.require(
        pm=path_manager,
        meta_path=processed_meta,
        expected_payload_path=processed_path,
    )
    assert context == _context(trade_date)
    assert loaded.payload_path == processed_path
    assert pq.ParquetFile(processed_path).read().num_rows == 0


def test_fact_step_rejects_an_empty_non_event_output(tmp_path: Path) -> None:
    path_manager = PathManager(tmp_path)
    trade_date = "2026-07-20"
    raw_path = path_manager.raw_payload(
        broker="broker",
        source_name="daily_bar",
        trade_date=trade_date,
        payload_file="data.parquet",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"empty-daily-bar")
    meta.commit(pm=path_manager, payload_path=raw_path)

    def normalize_operation(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        return NormalizeOutput(table=pa.table({"symbol": []}))

    step = FactMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"daily_bar": _source("daily_bar")},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock()},
        ),
        normalize_operations={"broker": normalize_operation},
        processed_version="v1",
        adapter_cache={},
    )

    with pytest.raises(
        ValueError,
        match=(
            "FactNormalize source=daily_bar target=daily_bar "
            "trade_date=2026-07-20: data must contain at least one row"
        ),
    ):
        step.run(_context(trade_date))

    processed_meta = path_manager.processed_meta(
        dataset_name="daily_bar",
        version="v1",
        trade_date=trade_date,
    )
    assert meta.find(pm=path_manager, meta_path=processed_meta) is None


def test_fact_step_rejects_unbound_normalizer_before_date_io(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="not bound"):
        FactMaterializeStep(
            app_config=cast("AppConfig", object()),
            path_manager=PathManager(tmp_path),
            sources={"source": _source("source")},
            broker_classes=cast(
                "dict[str, type[BrokerAdapter]]",
                {"broker": Mock()},
            ),
            normalize_operations={},
            processed_version="v1",
            adapter_cache={},
        )
