# filepath: tests/data_system/steps/test_stock_1430_daily_l2_build.py
"""H04 publication, calendar, no-leakage and recovery boundaries."""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.builders.stock_1430 import STOCK_1430_FEATURE_SCHEMA
from src.data_system.builders.stock_1430_daily_l2 import STOCK_1430_DAILY_SOURCE_COLUMNS
from src.data_system.context import DataContext
from src.data_system.steps import stock_1430_daily_l2_build as step_module
from src.data_system.steps.stock_1430_daily_l2_build import Stock1430DailyL2BuildStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import ObjectPaths, PathManager


def _publish(pm: PathManager, paths: ObjectPaths, table: pa.Table) -> None:
    paths.payload_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, paths.payload_path)
    meta.commit(pm=pm, payload_path=paths.payload_path)


def _calendar(pm: PathManager, sessions: tuple[str, ...]) -> None:
    for year in sorted({int(session[:4]) for session in sessions}):
        first = date(year, 1, 1)
        days = (date(year + 1, 1, 1) - first).days
        dates = [(first + timedelta(days=offset)).isoformat() for offset in range(days)]
        _publish(
            pm,
            pm.processed_year_object(
                dataset_name="trade_calendar", version="v1", calendar_year=year
            ),
            pa.table(
                {"trade_date": dates, "is_open": [day in sessions for day in dates]}
            ),
        )


def _inputs(
    pm: PathManager, previous: str, target: str
) -> tuple[ObjectPaths, ObjectPaths, ObjectPaths]:
    l2 = pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=target)
    daily = pm.feature_object(
        feature_set="tushare_daily_basic", version="v1", trade_date=previous
    )
    output = pm.feature_object(
        feature_set="stock_1430_daily_l2", version="v1", trade_date=target
    )
    decision = DateTimeUtils.local_time_to_utc_epoch_us(
        time(14, 30), date.fromisoformat(target)
    )
    _publish(
        pm,
        l2,
        pa.Table.from_arrays(
            [
                pa.array(["000001", "000002"]),
                pa.array([target, target]),
                pa.array([decision, decision], type=pa.int64()),
                *[pa.array([0.5, 1.0], type=pa.float64()) for _ in range(32)],
            ],
            schema=STOCK_1430_FEATURE_SCHEMA,
        ),
    )
    _publish(
        pm,
        daily,
        pa.table(
            {
                "symbol": ["000001", "999999"],
                "trade_date": [previous, previous],
                **{
                    column: pa.array([1.0, 2.0])
                    for column in STOCK_1430_DAILY_SOURCE_COLUMNS
                },
            }
        ),
    )
    return l2, daily, output


@pytest.mark.parametrize(
    ("previous", "target"),
    (
        ("2025-11-17", "2025-11-18"),
        ("2025-12-31", "2026-01-05"),
        ("2026-04-30", "2026-05-06"),
        ("2026-07-24", "2026-07-27"),
    ),
)
def test_step_resolves_calendar_and_ignores_daily_t_for_new_outputs(
    tmp_path: Path, previous: str, target: str
) -> None:
    outputs = []
    for variant in ("absent", "invalid"):
        root = tmp_path / variant
        root.mkdir()
        pm = PathManager(root)
        _calendar(pm, (previous, target))
        l2, _, output = _inputs(pm, previous, target)
        if variant == "invalid":
            daily_t = pm.feature_object(
                feature_set="tushare_daily_basic", version="v1", trade_date=target
            )
            daily_t.meta_path.parent.mkdir(parents=True)
            daily_t.meta_path.write_text("invalid JSON: must never be read")
        step = Stock1430DailyL2BuildStep(
            pm=pm, access=Access(pm, processed_version="v1")
        )
        context = DataContext(start=target, end=target, trade_dates=(target,))

        assert step.run(context) is context

        with pq.ParquetFile(output.payload_path) as reader:
            table = reader.read()
        with pq.ParquetFile(l2.payload_path) as reader:
            l2_table = reader.read()
        assert table.num_columns == 42
        assert table.select(l2_table.column_names).equals(l2_table)
        assert all(
            table.column(f"{column}_rank").to_pylist() == [1.0, None]
            for column in STOCK_1430_DAILY_SOURCE_COLUMNS
        )
        assert set(json.loads(output.meta_path.read_text())) == {
            "payload",
            "size_bytes",
        }
        assert not list(root.glob("labels/**/*.parquet"))
        outputs.append(table)
    assert outputs[0].equals(outputs[1])


def test_step_reuses_without_calendar_upstreams_or_parquet_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = PathManager(tmp_path)
    previous, target = "2025-12-31", "2026-01-05"
    _calendar(pm, (previous, target))
    l2, daily, output = _inputs(pm, previous, target)
    context = DataContext(start=target, end=target, trade_dates=(target,))
    access = Access(pm, processed_version="v1")
    step = Stock1430DailyL2BuildStep(pm=pm, access=access)
    step.run(context)
    before = {
        path: (path.read_bytes(), path.stat())
        for path in (output.meta_path, output.payload_path)
    }
    l2.meta_path.unlink()
    daily.payload_path.write_bytes(b"changed upstream")
    prior_calendar = pm.processed_year_object(
        dataset_name="trade_calendar", version="v1", calendar_year=2025
    )
    prior_calendar.meta_path.unlink()
    monkeypatch.setattr(
        access,
        "recent_trade_dates",
        Mock(side_effect=AssertionError("P must not be resolved")),
    )
    monkeypatch.setattr(
        step_module.pq,
        "ParquetFile",
        Mock(side_effect=AssertionError("payload must not be opened")),
    )

    assert step.run(context) is context
    for path, (content, stat) in before.items():
        assert path.read_bytes() == content
        after = path.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
        )


@pytest.mark.parametrize("input_name", ("l2", "daily"))
@pytest.mark.parametrize(
    "invalid", ("missing_meta", "missing_payload", "size", "schema", "relationship")
)
def test_step_requires_both_valid_inputs_and_never_falls_back(
    tmp_path: Path, input_name: str, invalid: str
) -> None:
    pm = PathManager(tmp_path)
    previous, target = "2026-04-30", "2026-05-06"
    _calendar(pm, ("2026-04-29", previous, target))
    l2, daily, output = _inputs(pm, previous, target)
    _inputs(pm, "2026-04-29", "2026-04-29")
    _publish(
        pm,
        pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date=target
        ),
        pa.table({"wrong": [1]}),
    )
    broken = l2 if input_name == "l2" else daily
    if invalid == "missing_meta":
        broken.meta_path.unlink()
    elif invalid == "missing_payload":
        broken.payload_path.unlink()
    elif invalid == "size":
        broken.payload_path.write_bytes(b"wrong size")
    elif invalid == "schema":
        _publish(pm, broken, pa.table({"wrong_column": [1]}))
    else:
        upstream = pm.processed_year_object(
            dataset_name="trade_calendar", version="v1", calendar_year=2026
        )
        meta.commit(
            pm=pm,
            payload_path=broken.payload_path,
            upstream_meta_path=upstream.meta_path,
        )

    with pytest.raises((FileNotFoundError, ValueError, RuntimeError)):
        Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1")).run(
            DataContext(start=target, end=target, trade_dates=(target,))
        )
    assert not output.meta_path.exists()
    assert not output.payload_path.exists()


@pytest.mark.parametrize(
    "invalid", ("json", "size", "payload", "upstream", "symbol_slices")
)
def test_step_rejects_invalid_output_meta_without_overwriting_or_input_reads(
    tmp_path: Path, invalid: str
) -> None:
    pm = PathManager(tmp_path)
    _, _, output = _inputs(pm, "2026-04-30", "2026-05-06")
    _publish(pm, output, pa.table({"existing": [1]}))
    document = json.loads(output.meta_path.read_text())
    if invalid == "json":
        output.meta_path.write_text("invalid JSON")
    else:
        if invalid == "size":
            document["size_bytes"] += 1
        elif invalid == "payload":
            other = output.payload_path.with_name("other.parquet")
            other.write_bytes(output.payload_path.read_bytes())
            document["payload"] = other.name
        elif invalid == "upstream":
            daily = pm.feature_object(
                feature_set="tushare_daily_basic", version="v1", trade_date="2026-04-30"
            )
            document["upstream"] = {
                "meta_path": daily.meta_path.relative_to(tmp_path).as_posix(),
                "size_bytes": daily.payload_path.stat().st_size,
            }
        else:
            document["symbol_slices"] = {"000001": {"start": 0, "end": 1}}
        output.meta_path.write_text(json.dumps(document))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (output.meta_path, output.payload_path)
    }
    access = Mock(spec=Access)
    with pytest.raises(RuntimeError):
        Stock1430DailyL2BuildStep(pm=pm, access=access).run(
            DataContext(
                start="2026-05-06", end="2026-05-06", trade_dates=("2026-05-06",)
            )
        )
    access.recent_trade_dates.assert_not_called()
    for path, snapshot in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == snapshot


def test_step_preserves_earlier_partition_and_resumes_after_missing_input(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    sessions = ("2026-04-30", "2026-05-06", "2026-05-07")
    _calendar(pm, sessions)
    _, _, first = _inputs(pm, sessions[0], sessions[1])
    _, missing, second = _inputs(pm, sessions[1], sessions[2])
    saved_meta = missing.meta_path.read_bytes()
    missing.meta_path.unlink()
    context = DataContext(start=sessions[1], end=sessions[2], trade_dates=sessions[1:])
    step = Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1"))
    with pytest.raises(FileNotFoundError):
        step.run(context)
    assert first.meta_path.is_file()
    assert not second.meta_path.exists()
    before = (first.payload_path.read_bytes(), first.payload_path.stat().st_mtime_ns)
    missing.meta_path.write_bytes(saved_meta)
    assert step.run(context) is context
    assert second.meta_path.is_file()
    assert (
        first.payload_path.read_bytes(),
        first.payload_path.stat().st_mtime_ns,
    ) == before


@pytest.mark.parametrize("invalid_schema", (False, True))
def test_step_closes_parquet_readers_on_success_and_builder_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_schema: bool
) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm, ("2026-04-30", "2026-05-06"))
    l2, _, _ = _inputs(pm, "2026-04-30", "2026-05-06")
    if invalid_schema:
        _publish(pm, l2, pa.table({"invalid": [1]}))
    opened = []
    parquet_file = pq.ParquetFile

    def track(path: Path) -> pq.ParquetFile:
        reader = parquet_file(path)
        opened.append(reader)
        return reader

    monkeypatch.setattr(step_module.pq, "ParquetFile", track)
    step = Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1"))
    context = DataContext(
        start="2026-05-06", end="2026-05-06", trade_dates=("2026-05-06",)
    )
    if invalid_schema:
        with pytest.raises(ValueError, match="schema"):
            step.run(context)
    else:
        step.run(context)
    assert opened
    assert all(reader.closed for reader in opened)


@pytest.mark.parametrize("empty_input", ("l2", "daily"))
def test_step_rejects_empty_input_without_publishing(
    tmp_path: Path, empty_input: str
) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm, ("2026-04-30", "2026-05-06"))
    l2, daily, output = _inputs(pm, "2026-04-30", "2026-05-06")
    paths = l2 if empty_input == "l2" else daily
    with pq.ParquetFile(paths.payload_path) as reader:
        empty = reader.read().slice(0, 0)
    _publish(pm, paths, empty)
    with pytest.raises(ValueError, match="at least one row"):
        Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1")).run(
            DataContext(
                start="2026-05-06", end="2026-05-06", trade_dates=("2026-05-06",)
            )
        )
    assert not output.meta_path.exists()
    assert not output.payload_path.exists()


def test_step_publishes_all_null_ranks_and_does_not_reuse_orphan_payload(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm, ("2026-04-30", "2026-05-06"))
    _, daily, output = _inputs(pm, "2026-04-30", "2026-05-06")
    with pq.ParquetFile(daily.payload_path) as reader:
        outside_universe = reader.read().slice(1, 1)
    _publish(pm, daily, outside_universe)
    output.payload_path.parent.mkdir(parents=True)
    output.payload_path.write_bytes(b"uncommitted payload")
    Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1")).run(
        DataContext(start="2026-05-06", end="2026-05-06", trade_dates=("2026-05-06",))
    )
    record = meta.require(
        pm=pm, meta_path=output.meta_path, expected_payload_path=output.payload_path
    )
    with pq.ParquetFile(record.payload_path) as reader:
        result = reader.read()
    assert result.num_rows == 2
    assert result.num_columns == 42
    assert all(
        result.column(f"{column}_rank").null_count == 2
        for column in STOCK_1430_DAILY_SOURCE_COLUMNS
    )


def test_step_requires_previous_year_calendar_on_miss(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm, ("2024-12-31", "2026-01-05"))
    _, _, output = _inputs(pm, "2025-12-31", "2026-01-05")
    with pytest.raises(FileNotFoundError):
        Stock1430DailyL2BuildStep(pm=pm, access=Access(pm, processed_version="v1")).run(
            DataContext(
                start="2026-01-05", end="2026-01-05", trade_dates=("2026-01-05",)
            )
        )
    assert not output.meta_path.exists()
