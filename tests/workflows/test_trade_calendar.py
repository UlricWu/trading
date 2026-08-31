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


def test_bootstrap_runs_only_calendar_materialization_from_2016_to_as_of_year(
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
        as_of_date="2026-08-21",
    )

    assert [
        type(step).__name__ for step in pipeline_factory.call_args.kwargs["steps"]
    ] == ["CalendarMaterializeStep"]
    pipeline.run.assert_called_once_with(
        DataContext(start="2016-01-01", end="2026-12-31")
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "▶️ workflow; kind=data-calendar start=2016-01-01 "
        "end=2026-12-31 as_of_date=2026-08-21",
        "✅ workflow; kind=data-calendar start=2016-01-01 "
        "end=2026-12-31 as_of_date=2026-08-21",
    ]


def test_bootstrap_rejects_invalid_as_of_before_pipeline_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)

    with pytest.raises(ValueError, match="as_of_date"):
        run_trade_calendar_bootstrap(
            app_config=cast("AppConfig", object()),
            path_manager=cast("PathManager", object()),
            as_of_date="2026-02-30",
        )

    pipeline_factory.assert_not_called()
