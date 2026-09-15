# filepath: tests/workflows/test_trade_calendar.py
"""Composition tests for the fixed annual trade-calendar bootstrap."""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock

import pytest

from src.config.app_config import AppConfig
from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.utils.path import PathManager
from src.workflows import trade_calendar as workflow_module
from src.workflows.trade_calendar import run_trade_calendar_bootstrap


@pytest.mark.parametrize(
    ("as_of_date", "end_date"),
    [
        ("2016-01-01", "2016-12-31"),
        ("2026-08-21", "2026-12-31"),
    ],
)
def test_bootstrap_runs_only_calendar_materialization_from_2016_to_as_of_year(
    as_of_date: str,
    end_date: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)

    run_trade_calendar_bootstrap(
        app_config=cast("AppConfig", object()),
        path_manager=cast("PathManager", object()),
        as_of_date=as_of_date,
    )

    assert [
        type(step).__name__ for step in pipeline_factory.call_args.kwargs["steps"]
    ] == ["CalendarMaterializeStep"]
    pipeline.run.assert_called_once_with(
        DataContext(start="2016-01-01", end=end_date)
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        f"▶️ workflow; kind=data-calendar start=2016-01-01 "
        f"end={end_date} as_of_date={as_of_date}",
        f"✅ workflow; kind=data-calendar start=2016-01-01 "
        f"end={end_date} as_of_date={as_of_date}",
    ]


@pytest.mark.parametrize("as_of_date", ["2026-02-30", "2026-7-01", "2015-12-31"])
def test_bootstrap_rejects_invalid_as_of_before_pipeline_preparation(
    as_of_date: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    access_factory = Mock()
    pipeline_factory = Mock()
    instrumentation_factory = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    monkeypatch.setattr(workflow_module, "Access", access_factory)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)
    monkeypatch.setattr(workflow_module, "Instrumentation", instrumentation_factory)

    with pytest.raises(ValueError, match="as_of_date"):
        run_trade_calendar_bootstrap(
            app_config=cast("AppConfig", object()),
            path_manager=cast("PathManager", object()),
            as_of_date=as_of_date,
        )

    access_factory.assert_not_called()
    pipeline_factory.assert_not_called()
    instrumentation_factory.assert_not_called()
    assert not logger.mock_calls
