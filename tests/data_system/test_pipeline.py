# filepath: tests/data_system/test_pipeline.py
"""Behavior tests for offline data completion and skip semantics."""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock

import pytest

from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline, DataRunStatus
from src.observability.instrumentation import NoOpInstrumentation
from src.pipeline.step import PipelineStep
from src.utils.path import PathManager


def test_pipeline_returns_success_after_all_steps_complete() -> None:
    ingest = Mock(spec=PipelineStep)
    normalize = Mock(spec=PipelineStep)
    ingest.run.side_effect = lambda context: context
    normalize.run.side_effect = lambda context: context
    pipeline = DataPipeline(
        steps=[ingest, normalize],
        pm=cast("PathManager", object()),
        inst=NoOpInstrumentation(),
    )

    status = pipeline.run("2026-07-20")

    assert status is DataRunStatus.SUCCESS
    ingest.run.assert_called_once()
    normalize.run.assert_called_once()


def test_pipeline_skips_only_when_ingest_returns_no_context() -> None:
    ingest = Mock(spec=PipelineStep)
    normalize = Mock(spec=PipelineStep)
    ingest.run.return_value = None
    pipeline = DataPipeline(
        steps=[ingest, normalize],
        pm=cast("PathManager", object()),
        inst=NoOpInstrumentation(),
    )

    status = pipeline.run("2026-07-20")

    assert status is DataRunStatus.SKIPPED
    normalize.run.assert_not_called()


def test_pipeline_rejects_no_context_from_later_step() -> None:
    context = Mock(spec=DataContext)
    ingest = Mock(spec=PipelineStep)
    normalize = Mock(spec=PipelineStep)
    ingest.run.return_value = context
    normalize.run.return_value = None
    pipeline = DataPipeline(
        steps=[ingest, normalize],
        pm=cast("PathManager", object()),
        inst=NoOpInstrumentation(),
    )

    with pytest.raises(RuntimeError, match="returned no context"):
        pipeline.run("2026-07-20")

