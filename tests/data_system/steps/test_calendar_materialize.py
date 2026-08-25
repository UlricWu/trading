# filepath: tests/data_system/steps/test_calendar_materialize.py
"""Behavior tests for annual calendar materialization."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter
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
    monkeypatch.setattr(calendar_module, "logs", logger)
    path_manager = PathManager(tmp_path)
    broker = _CalendarBroker()
    access = Mock(spec=Access)
    access.trade_dates.return_value = ["2026-01-02"]
    step = CalendarMaterializeStep(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        adapter_cache={"tushare": cast("BrokerAdapter", broker)},
    )
    context = DataContext(start="2025-12-20", end="2026-01-10")

    result = step.run(context)
    step.run(DataContext(start="2025-12-20", end="2026-01-10"))

    assert result is context
    assert context.trade_dates == ("2026-01-02",)
    assert broker.calendar_years == [2025, 2026]
    messages = [call.args[0] for call in logger.info.call_args_list]
    assert sum(message.startswith("✅ calendar publish;") for message in messages) == 2
    assert [message.split(";", 1)[0] for message in messages] == [
        "✅ calendar publish",
        "✅ calendar publish",
        "✅ calendar materialize",
        "♻️ calendar processed meta hit",
        "♻️ calendar processed meta hit",
        "✅ calendar materialize",
    ]
    assert messages[2] == (
        "✅ calendar materialize; years=2 reused=0 published=2 trade_dates=1"
    )
    assert messages[-1] == (
        "✅ calendar materialize; years=2 reused=2 published=0 trade_dates=1"
    )
    processed_meta_by_year = {
        calendar_year: path_manager.processed_year_meta(
            dataset_name="trade_calendar",
            version="v1",
            calendar_year=calendar_year,
        )
        for calendar_year in (2025, 2026)
    }
    for offset, calendar_year in enumerate((2025, 2026), start=3):
        assert messages[offset] == (
            f"♻️ calendar processed meta hit; calendar_year={calendar_year} "
            f"meta={processed_meta_by_year[calendar_year]}"
        )
    access.trade_dates.assert_any_call(
        start_date="2025-12-20",
        end_date="2026-01-10",
    )

    for calendar_year in (2025, 2026):
        processed_payload = path_manager.processed_year_data(
            dataset_name="trade_calendar",
            version="v1",
            calendar_year=calendar_year,
        )
        assert pq.read_table(processed_payload).to_pydict() == {
            "trade_date": [
                f"{calendar_year}-01-01",
                f"{calendar_year}-01-02",
            ],
            "is_open": [False, True],
        }
        processed = meta.require(
            pm=path_manager,
            meta_path=path_manager.processed_year_meta(
                dataset_name="trade_calendar",
                version="v1",
                calendar_year=calendar_year,
            ),
            expected_payload_path=processed_payload,
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
    monkeypatch.setattr(calendar_module, "logs", logger)
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
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        access=access,
        processed_version="v1",
        adapter_cache={},
    )

    result = step.run(DataContext(start="2026-01-01", end="2026-01-02"))

    assert result.trade_dates == ("2026-01-02",)
    processed_payload = path_manager.processed_year_data(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=calendar_year,
    )
    messages = [call.args[0] for call in logger.info.call_args_list]
    assert messages == [
        f"♻️ calendar raw meta hit; calendar_year=2026 meta={raw_meta}",
        "✅ calendar publish; calendar_year=2026 "
        f"output={processed_payload}",
        "✅ calendar materialize; years=1 reused=0 published=1 trade_dates=1",
    ]
