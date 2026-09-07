# filepath: tests/data_system/steps/test_calendar_materialize.py
"""Behavior tests for annual calendar materialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src import logs
from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps import calendar_materialize as calendar_module
from src.data_system.steps.calendar_materialize import CalendarMaterializeStep
from src.utils.path import PathManager


class _CalendarBroker:
    def __init__(self) -> None:
        self.calendar_years: list[int] = []

    def fetch_trade_calendar(
        self,
        *,
        calendar_year: int,
        pm: PathManager,
    ) -> Path:
        self.calendar_years.append(calendar_year)
        payload = pm.raw_year_payload(
            broker="tushare",
            source_name="trade_calendar",
            calendar_year=calendar_year,
            payload_file="data.parquet",
        )
        payload.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "cal_date": [f"{calendar_year}0101", f"{calendar_year}0102"],
                "is_open": [0, 1],
            }
        ).to_parquet(payload, index=False)
        return payload


def test_calendar_step_builds_missing_years_and_resolves_trade_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)
    path_manager = PathManager(tmp_path)
    broker = _CalendarBroker()
    get_broker = Mock(return_value=broker)
    access = Mock(spec=Access)
    access.trade_dates.return_value = ["2026-01-02"]
    step = CalendarMaterializeStep(
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        get_broker=get_broker,
    )
    context = DataContext(start="2025-12-20", end="2026-01-10")

    result = step.run(context)
    get_broker.side_effect = AssertionError("Broker must not be requested on reuse")
    monkeypatch.setattr(
        calendar_module,
        "normalize_tushare",
        Mock(side_effect=AssertionError("Calendar must not be normalized on reuse")),
    )
    step.run(DataContext(start="2025-12-20", end="2026-01-10"))

    assert result is context
    assert context.trade_dates == ("2026-01-02",)
    assert broker.calendar_years == [2025, 2026]
    messages = [
        call.args[0]
        for call in logger.info.call_args_list
        if call.args[0].startswith(("✅ calendar", "♻️ calendar"))
    ]
    assert sum(message.startswith("✅ calendar;") for message in messages) == 2
    assert [message.split(";", 1)[0] for message in messages] == [
        "✅ calendar",
        "✅ calendar",
        "✅ calendar materialize",
        "♻️ calendar",
        "♻️ calendar",
        "✅ calendar materialize",
    ]
    assert messages[2] == "✅ calendar materialize; years=2 trade_dates=1"
    assert messages[-1] == "✅ calendar materialize; years=2 trade_dates=1"
    processed_payload_by_year = {
        calendar_year: path_manager.processed_year_object(
            dataset_name="trade_calendar",
            version="v1",
            calendar_year=calendar_year,
        ).payload_path
        for calendar_year in (2025, 2026)
    }
    for offset, calendar_year in enumerate((2025, 2026), start=3):
        assert messages[offset] == (
            f"♻️ calendar; calendar_year={calendar_year} "
            f"output={processed_payload_by_year[calendar_year]}"
        )
    access.trade_dates.assert_any_call(
        start_date="2025-12-20",
        end_date="2026-01-10",
    )

    for calendar_year in (2025, 2026):
        processed_paths = path_manager.processed_year_object(
            dataset_name="trade_calendar",
            version="v1",
            calendar_year=calendar_year,
        )
        assert pq.read_table(processed_paths.payload_path).to_pydict() == {
            "trade_date": [
                f"{calendar_year}-01-01",
                f"{calendar_year}-01-02",
            ],
            "is_open": [False, True],
        }
        processed = meta.require(
            pm=path_manager,
            meta_path=processed_paths.meta_path,
            expected_payload_path=processed_paths.payload_path,
        )
        assert processed.upstream is not None
        assert str(processed.upstream[0]) == (
            f"raw/tushare/trade_calendar/year={calendar_year}/meta.json"
        )


def test_calendar_step_reports_raw_meta_hit_before_processed_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)
    path_manager = PathManager(tmp_path)
    calendar_year = 2026
    raw_payload = _CalendarBroker().fetch_trade_calendar(
        calendar_year=calendar_year,
        pm=path_manager,
    )
    meta.commit(pm=path_manager, payload_path=raw_payload)
    raw_meta = path_manager.raw_year_meta(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=calendar_year,
    )
    access = Mock(spec=Access)
    access.trade_dates.return_value = ["2026-01-02"]
    step = CalendarMaterializeStep(
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        get_broker=Mock(),
    )

    result = step.run(DataContext(start="2026-01-01", end="2026-01-02"))

    assert result.trade_dates == ("2026-01-02",)
    processed_payload = path_manager.processed_year_object(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=calendar_year,
    ).payload_path
    messages = [
        call.args[0]
        for call in logger.info.call_args_list
        if call.args[0].startswith(("✅ calendar", "♻️ calendar"))
    ]
    assert messages == [
        f"♻️ calendar raw meta hit; calendar_year=2026 meta={raw_meta}",
        f"✅ calendar; calendar_year=2026 output={processed_payload} rows=2",
        "✅ calendar materialize; years=1 trade_dates=1",
    ]


def test_calendar_step_rejects_empty_reused_raw_before_processed_publication(
    tmp_path: Path,
) -> None:
    path_manager = PathManager(tmp_path)
    raw_payload = path_manager.raw_year_payload(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
        payload_file="data.parquet",
    )
    raw_payload.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "cal_date": pa.array([], type=pa.string()),
                "is_open": pa.array([], type=pa.int64()),
            }
        ),
        raw_payload,
    )
    meta.commit(pm=path_manager, payload_path=raw_payload)
    access = Mock(spec=Access)
    access.trade_dates.return_value = []
    step = CalendarMaterializeStep(
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        get_broker=Mock(),
    )

    with pytest.raises(ValueError, match="at least one row"):
        step.run(DataContext(start="2026-01-01", end="2026-01-02"))

    processed_paths = path_manager.processed_year_object(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=2026,
    )
    assert not processed_paths.payload_path.exists()
    assert not processed_paths.meta_path.exists()
    access.trade_dates.assert_not_called()


@pytest.mark.parametrize(
    "failure_stage", ("unavailable", "fetch", "raw_meta", "normalize")
)
def test_calendar_step_preserves_raw_progress_and_stops_before_processed_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    path_manager = PathManager(tmp_path)
    failure = RuntimeError("calendar preparation failed")
    broker = Mock(spec=calendar_module.TushareBroker)
    broker.fetch_trade_calendar.side_effect = _CalendarBroker().fetch_trade_calendar
    if failure_stage == "unavailable":
        broker.fetch_trade_calendar.side_effect = None
        broker.fetch_trade_calendar.return_value = None
    elif failure_stage == "fetch":
        broker.fetch_trade_calendar.side_effect = failure
    elif failure_stage == "raw_meta":
        monkeypatch.setattr(meta, "commit", Mock(side_effect=failure))
    else:
        monkeypatch.setattr(
            calendar_module, "normalize_tushare", Mock(side_effect=failure)
        )
    logger = Mock()
    monkeypatch.setattr(logs, "info", logger.info)
    access = Mock(spec=Access)
    step = CalendarMaterializeStep(
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        get_broker=Mock(return_value=broker),
    )

    with pytest.raises(RuntimeError) as caught:
        step.run(DataContext(start="2026-01-01", end="2026-01-02"))

    if failure_stage == "unavailable":
        assert str(caught.value) == "trade_calendar is unavailable; calendar_year=2026"
    else:
        assert caught.value is failure
    raw_payload = path_manager.raw_year_payload(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
        payload_file="data.parquet",
    )
    raw_meta = path_manager.raw_year_meta(
        broker="tushare", source_name="trade_calendar", calendar_year=2026
    )
    assert raw_payload.exists() is (failure_stage in ("raw_meta", "normalize"))
    assert raw_meta.exists() is (failure_stage == "normalize")
    processed_paths = path_manager.processed_year_object(
        dataset_name="trade_calendar", version="v1", calendar_year=2026
    )
    assert not processed_paths.payload_path.exists()
    assert not processed_paths.meta_path.exists()
    access.trade_dates.assert_not_called()
    logger.info.assert_not_called()
