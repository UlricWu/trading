# filepath: tests/data_system/steps/test_fact_materialize.py
"""Behavior tests for range fact materialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, call

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOutput
from src.data_system.steps import _partition as partition_module
from src.data_system.steps import fact_materialize as fact_module
from src.data_system.steps.fact_materialize import FactMaterializeStep
from src.utils.path import ObjectPaths, PathManager


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = PathManager(tmp_path)
    raw_path = path_manager.raw_payload(
        broker="broker",
        source_name="available",
        trade_date="2026-07-20",
        payload_file="data.parquet",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw-payload")
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = [None, raw_path]
    get_broker = Mock(return_value=adapter)
    commit = Mock(wraps=meta.commit)
    monkeypatch.setattr(fact_module.meta, "commit", commit)
    normalize = Mock()
    step = FactMaterializeStep(
        path_manager=path_manager,
        sources={
            "missing": _source("missing"),
            "available": _source("available"),
        },
        get_broker=get_broker,
        normalize_operation=normalize,
        processed_version="v1",
    )

    with pytest.raises(RuntimeError, match="only partially available"):
        step.run(_context("2026-07-20"))

    assert adapter.fetch.call_count == 2
    assert get_broker.call_args_list == [call(), call()]
    commit.assert_called_once_with(pm=path_manager, payload_path=raw_path)
    normalize.assert_not_called()
    assert raw_path.with_name("meta.json").is_file()


def test_fact_step_reports_all_wholly_missing_dates_without_normalizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.return_value = None
    get_broker = Mock(return_value=adapter)
    normalize = Mock()
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=None))
    step = FactMaterializeStep(
        path_manager=path_manager,
        sources={"bars": _source("bars")},
        get_broker=get_broker,
        normalize_operation=normalize,
        processed_version="v1",
    )

    with pytest.raises(
        RuntimeError,
        match=r"missing_dates=\['2026-07-20', '2026-07-21'\]",
    ):
        step.run(_context("2026-07-20", "2026-07-21"))

    assert [call.kwargs["trade_date"] for call in adapter.fetch.call_args_list] == [
        "2026-07-20",
        "2026-07-21",
    ]
    assert get_broker.call_args_list == [call(), call()]
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
    get_broker = Mock()
    normalize = Mock()
    monkeypatch.setattr(fact_module.meta, "find", Mock(return_value=object()))
    step = FactMaterializeStep(
        path_manager=path_manager,
        sources={"bars": _source("bars", outputs=[])},
        get_broker=get_broker,
        normalize_operation=normalize,
        processed_version="v1",
    )
    context = _context("2026-07-20")

    assert step.run(context) is context
    get_broker.assert_not_called()
    normalize.assert_not_called()
    path_manager.processed_object.assert_not_called()
    assert [call.args[0] for call in logger.info.call_args_list] == [
        (
            f"♻️ raw meta hit; source=bars broker=broker trade_date=2026-07-20 "
            f"meta={raw_meta_path}"
        ),
        "✅ fact date; trade_date=2026-07-20 elapsed_seconds=0.250",
        (
            "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
            "processed_reused=0 processed_published=0 unavailable=0"
        ),
    ]


def test_fact_step_uses_bound_dependencies_for_all_sources_and_outputs(
    tmp_path: Path,
) -> None:
    path_manager = PathManager(tmp_path)
    sources = {
        "first": _source("raw_first", outputs=["first_a", "first_b"]),
        "raw_only": _source("raw_only", outputs=[]),
        "last": _source("raw_last", outputs=["last"]),
    }
    context = _context("2026-07-20", "2026-07-21")

    def fetch(
        *, source_name: str, raw_object: str, trade_date: str, pm: PathManager
    ) -> Path:
        raw_path = pm.raw_payload(
            broker="broker",
            source_name=source_name,
            trade_date=trade_date,
            payload_file="data.parquet",
        )
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(b"raw-payload")
        return raw_path

    def normalize_operation(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        for source_name in sources:
            meta.require(
                pm=path_manager,
                meta_path=path_manager.raw_meta(
                    broker="broker",
                    source_name=source_name,
                    trade_date=trade_date,
                ),
            )
        return NormalizeOutput(table=pa.table({"value": [1]}))

    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = fetch
    get_broker = Mock(return_value=adapter)
    normalize = Mock(side_effect=normalize_operation)
    step = FactMaterializeStep(
        path_manager=path_manager,
        sources=sources,
        get_broker=get_broker,
        normalize_operation=normalize,
        processed_version="v1",
    )

    get_broker.assert_not_called()
    assert step.run(context) is context
    assert step.run(context) is context

    assert get_broker.call_count == 6
    assert adapter.fetch.call_args_list == [
        call(
            source_name=source_name,
            trade_date=trade_date,
            raw_object=source.raw_object,
            pm=path_manager,
        )
        for trade_date in context.trade_dates
        for source_name, source in sources.items()
    ]
    assert normalize.call_args_list == [
        call(
            input_file=path_manager.raw_payload(
                broker="broker",
                source_name=source_name,
                trade_date=trade_date,
                payload_file="data.parquet",
            ),
            output_name=path_manager.processed_object(
                dataset_name=output, version="v1", trade_date=trade_date
            ).payload_path,
            raw_object=source.raw_object,
            target_name=output,
            trade_date=trade_date,
        )
        for trade_date in context.trade_dates
        for source_name, source in sources.items()
        for output in source.outputs
    ]
    expected_meta_paths: set[Path] = set()
    for trade_date in context.trade_dates:
        for output in ("first_a", "first_b", "last"):
            paths = path_manager.processed_object(
                dataset_name=output, version="v1", trade_date=trade_date
            )
            meta.require(
                pm=path_manager,
                meta_path=paths.meta_path,
                expected_payload_path=paths.payload_path,
            )
            assert pq.ParquetFile(paths.payload_path).read().to_pydict() == {
                "value": [1]
            }
            expected_meta_paths.add(paths.meta_path)
    assert set((tmp_path / "processed").rglob("meta.json")) == expected_meta_paths


@pytest.mark.parametrize("staging_payload", [None, b"same-size", b"short"])
def test_fact_step_selects_staging_only_when_it_matches_raw_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    staging_payload: bytes | None,
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
    if staging_payload is not None:
        staging_path.parent.mkdir(parents=True)
        staging_path.write_bytes(staging_payload)
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
        path_manager=path_manager,
        sources={"source": _source("raw_object", outputs=["output"])},
        get_broker=Mock(),
        normalize_operation=normalize_operation,
        processed_version="v1",
    )

    step.run(_context(trade_date))
    step.run(_context(trade_date))

    assert selected_inputs == [
        staging_path if staging_payload == b"same-size" else raw_path
    ]
    output_paths = path_manager.processed_object(
        dataset_name="output",
        version="v1",
        trade_date=trade_date,
    )
    raw_meta_path = path_manager.raw_meta(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        (
            f"♻️ raw meta hit; source=source broker=broker trade_date={trade_date} "
            f"meta={raw_meta_path}"
        ),
        (
            f"✅ processed publish; target=output source=source "
            f"trade_date={trade_date} rows=1 normalize_seconds=0.500 "
            f"output={output_paths.payload_path}"
        ),
        f"✅ fact date; trade_date={trade_date} elapsed_seconds=2.000",
        "\n".join(
            (
                "✅ ===== Fact operation summary =====",
                f"{'normalize output':<35} {0.5:>8.3f}s avg=0.500s runs=1",
            )
        ),
        (
            "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
            "processed_reused=0 processed_published=1 unavailable=0"
        ),
        (
            f"♻️ raw meta hit; source=source broker=broker trade_date={trade_date} "
            f"meta={raw_meta_path}"
        ),
        (
            f"♻️ processed meta hit; target=output source=source "
            f"trade_date={trade_date} meta={output_paths.meta_path}"
        ),
        f"✅ fact date; trade_date={trade_date} elapsed_seconds=0.100",
        (
            "✅ fact materialize; trade_dates=1 raw_reused=1 raw_fetched=0 "
            "processed_reused=1 processed_published=0 unavailable=0"
        ),
    ]


def test_fact_step_reuses_one_raw_record_and_input_selection_across_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    raw_path = pm.raw_payload(
        broker="broker",
        source_name="source",
        trade_date="2026-07-20",
        payload_file="source.csv.7z",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw-payload")
    meta.commit(pm=pm, payload_path=raw_path)
    raw_meta_path = pm.raw_meta(
        broker="broker", source_name="source", trade_date="2026-07-20"
    )
    find = Mock(wraps=meta.find)
    select_staging = Mock(wraps=pm.staging_payload)
    monkeypatch.setattr(meta, "find", find)
    monkeypatch.setattr(pm, "staging_payload", select_staging)
    get_broker = Mock()
    normalize = Mock(return_value=NormalizeOutput(table=pa.table({"value": [1]})))
    step = FactMaterializeStep(
        path_manager=pm,
        sources={"source": _source("raw_object", outputs=["first", "second"])},
        get_broker=get_broker,
        normalize_operation=normalize,
        processed_version="v1",
    )

    step.run(_context("2026-07-20"))

    get_broker.assert_not_called()
    assert [
        call.kwargs["meta_path"]
        for call in find.call_args_list
        if call.kwargs["meta_path"] == raw_meta_path
    ] == [raw_meta_path]
    select_staging.assert_called_once_with(
        broker="broker",
        source_name="source",
        trade_date="2026-07-20",
        payload_file="source.csv.7z",
    )
    assert [call.kwargs["input_file"] for call in normalize.call_args_list] == [
        raw_path,
        raw_path,
    ]
    for output in ("first", "second"):
        paths = pm.processed_object(
            dataset_name=output, version="v1", trade_date="2026-07-20"
        )
        loaded = meta.require(pm=pm, meta_path=paths.meta_path)
        assert loaded.upstream == (
            raw_meta_path.relative_to(pm.storage_root),
            raw_path.stat().st_size,
        )


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
    path_manager.processed_object.side_effect = lambda **kwargs: ObjectPaths(
        payload_path=Path(f"/processed/{kwargs['trade_date']}/data.parquet"),
        meta_path=Path(f"/processed/{kwargs['trade_date']}/meta.json"),
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
    monkeypatch.setattr(partition_module, "write_parquet_atomic", Mock())
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = lambda *, source_name, raw_object, trade_date, pm: Path(
        f"/raw/{trade_date}/data.parquet"
    )
    normalize = Mock(return_value=NormalizeOutput(table=pa.table({"value": [1]})))
    step = FactMaterializeStep(
        path_manager=path_manager,
        sources={"source": _source("raw_object", outputs=["output"])},
        get_broker=Mock(return_value=adapter),
        normalize_operation=normalize,
        processed_version="v1",
    )

    result = step.run(_context("2026-07-20", "2026-07-21"))

    assert result.trade_dates == ("2026-07-20", "2026-07-21")
    assert adapter.fetch.call_count == 2
    assert normalize.call_count == 2
    assert [call.args[0] for call in logger.info.call_args_list] == [
        (
            "✅ raw ingest; source=source broker=broker trade_date=2026-07-20 "
            "elapsed_seconds=2.000 output=/raw/2026-07-20/data.parquet"
        ),
        (
            "✅ processed publish; target=output source=source "
            "trade_date=2026-07-20 rows=1 normalize_seconds=3.000 "
            "output=/processed/2026-07-20/data.parquet"
        ),
        "✅ fact date; trade_date=2026-07-20 elapsed_seconds=8.000",
        (
            "✅ raw ingest; source=source broker=broker trade_date=2026-07-21 "
            "elapsed_seconds=5.000 output=/raw/2026-07-21/data.parquet"
        ),
        (
            "✅ processed publish; target=output source=source "
            "trade_date=2026-07-21 rows=1 normalize_seconds=4.000 "
            "output=/processed/2026-07-21/data.parquet"
        ),
        "✅ fact date; trade_date=2026-07-21 elapsed_seconds=12.000",
        "\n".join(
            (
                "✅ ===== Fact operation summary =====",
                f"{'raw ingest source':<35} {7.0:>8.3f}s avg=3.500s runs=2",
                f"{'normalize output':<35} {7.0:>8.3f}s avg=3.500s runs=2",
            )
        ),
        (
            "✅ fact materialize; trade_dates=2 raw_reused=0 raw_fetched=2 "
            "processed_reused=0 processed_published=2 unavailable=0"
        ),
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
            table=pa.table({"symbol": pa.array([], type=pa.string())})
        )

    step = FactMaterializeStep(
        path_manager=path_manager,
        sources={empty_source: _source(empty_source)},
        get_broker=Mock(),
        normalize_operation=normalize_operation,
        processed_version="v1",
    )

    context = step.run(_context(trade_date))

    processed_paths = path_manager.processed_object(
        dataset_name=empty_source,
        version="v1",
        trade_date=trade_date,
    )
    loaded = meta.require(
        pm=path_manager,
        meta_path=processed_paths.meta_path,
        expected_payload_path=processed_paths.payload_path,
    )
    assert context == _context(trade_date)
    assert loaded.payload_path == processed_paths.payload_path
    assert pq.ParquetFile(processed_paths.payload_path).read().num_rows == 0


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
        path_manager=path_manager,
        sources={"daily_bar": _source("daily_bar")},
        get_broker=Mock(),
        normalize_operation=normalize_operation,
        processed_version="v1",
    )

    with pytest.raises(
        ValueError,
        match=(
            "FactNormalize source=daily_bar target=daily_bar "
            "trade_date=2026-07-20: data must contain at least one row"
        ),
    ):
        step.run(_context(trade_date))

    processed_meta = path_manager.processed_object(
        dataset_name="daily_bar",
        version="v1",
        trade_date=trade_date,
    ).meta_path
    assert meta.find(pm=path_manager, meta_path=processed_meta) is None
