# filepath: tests/observability/test_instrumentation.py
"""Behavior tests for named-operation instrumentation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.observability import instrumentation as instrumentation_module
from src.observability.instrumentation import Instrumentation


def test_measure_returns_the_operation_result_and_forwards_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(instrumentation_module, "logs", logger)
    monkeypatch.setattr(
        instrumentation_module.time,
        "perf_counter",
        Mock(side_effect=[1.0, 1.25]),
    )
    operation = Mock(return_value={"status": "complete"})

    with Instrumentation("scope") as instrumentation:
        result = instrumentation.measure(
            "Load",
            operation,
            "2026-07-20",
            required=True,
        )

    assert result == {"status": "complete"}
    operation.assert_called_once_with("2026-07-20", required=True)
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "✅ pipeline step; step=Load total_seconds=0.250 "
        "average_seconds=0.250 runs=1 failed_runs=0",
        "✅ pipeline; scope=scope total_seconds=0.250 steps=1",
    ]
    logger.error.assert_not_called()


def test_measure_counts_failed_operations_and_preserves_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("failed")
    operation = Mock(side_effect=[None, error])
    logger = Mock()
    monkeypatch.setattr(instrumentation_module, "logs", logger)
    monkeypatch.setattr(
        instrumentation_module.time,
        "perf_counter",
        Mock(side_effect=[1.0, 1.2, 2.0, 2.3]),
    )

    with (
        pytest.raises(RuntimeError) as raised,
        Instrumentation("scope") as instrumentation,
    ):
        instrumentation.measure("Build", operation)
        instrumentation.measure("Build", operation)

    assert raised.value is error
    assert [call.args[0] for call in logger.error.call_args_list] == [
        "❌ pipeline step; step=Build total_seconds=0.500 "
        "average_seconds=0.250 runs=2 failed_runs=1",
        "❌ pipeline; scope=scope total_seconds=0.500 steps=1 "
        "error_type=RuntimeError",
    ]
    logger.info.assert_not_called()
