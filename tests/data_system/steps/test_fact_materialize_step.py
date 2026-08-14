# filepath: tests/data_system/steps/test_fact_materialize_step.py
"""Availability tests for range fact materialization."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from src.data_system.context import DataContext
from src.data_system.source_materializer import SourceMaterializer
from src.data_system.steps.fact_materialize_step import FactMaterializeStep


def _context() -> DataContext:
    return DataContext(
        start="2026-07-20",
        end="2026-07-21",
        trade_dates=("2026-07-20", "2026-07-21"),
    )


def test_fact_step_materializes_every_trade_date_in_order() -> None:
    materializer = Mock(spec=SourceMaterializer)
    materializer.materialize.return_value = True
    step = FactMaterializeStep(materializer=materializer)
    context = _context()

    result = step.run(context)

    assert result is context
    assert materializer.materialize.call_args_list == [
        call("2026-07-20"),
        call("2026-07-21"),
    ]


@pytest.mark.parametrize("availability", [[False, False], [True, False]])
def test_fact_step_rejects_any_missing_trade_date(
    availability: list[bool],
) -> None:
    materializer = Mock(spec=SourceMaterializer)
    materializer.materialize.side_effect = availability

    with pytest.raises(
        RuntimeError,
        match=r"unavailable missing_dates=\['2026-07-21'\]"
        if availability[0]
        else r"missing_dates=\['2026-07-20', '2026-07-21'\]",
    ):
        FactMaterializeStep(materializer=materializer).run(_context())

    assert materializer.materialize.call_args_list == [
        call("2026-07-20"),
        call("2026-07-21"),
    ]
