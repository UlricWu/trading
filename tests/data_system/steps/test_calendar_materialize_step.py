# filepath: tests/data_system/steps/test_calendar_materialize_step.py
"""Schedule tests for calendar materialization."""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock, call

import pytest

from src.access import Access
from src.data_system.context import DataContext
from src.data_system.source_materializer import SourceMaterializer
from src.data_system.steps.calendar_materialize_step import CalendarMaterializeStep


def test_calendar_step_materializes_natural_dates_then_resolves_trade_dates() -> None:
    materializer = Mock(spec=SourceMaterializer)
    materializer.materialize.return_value = True
    access = Mock(spec=Access)
    access.trade_dates.return_value = ["2026-07-20"]
    step = CalendarMaterializeStep(
        materializer=materializer,
        access=access,
    )
    context = DataContext(start="2026-07-18", end="2026-07-20")

    result = step.run(context)

    assert result is context
    assert context.trade_dates == ("2026-07-20",)
    assert materializer.materialize.call_args_list == [
        call("2026-07-18"),
        call("2026-07-19"),
        call("2026-07-20"),
    ]
    access.trade_dates.assert_called_once_with(
        start_date="2026-07-18",
        end_date="2026-07-20",
    )


def test_calendar_step_rejects_a_missing_calendar_payload() -> None:
    materializer = Mock(spec=SourceMaterializer)
    materializer.materialize.return_value = False
    step = CalendarMaterializeStep(
        materializer=materializer,
        access=cast("Access", object()),
    )

    with pytest.raises(RuntimeError, match="missing trade_calendar"):
        step.run(DataContext(start="2026-07-20", end="2026-07-20"))
