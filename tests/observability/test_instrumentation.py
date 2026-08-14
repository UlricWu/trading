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


def test_measure_counts_failed_operations_and_preserves_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("failed")
    operation = Mock(side_effect=[None, error])
    info = Mock()
    monkeypatch.setattr(instrumentation_module.logs, "info", info)
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
    messages = [call.args[0] for call in info.call_args_list]
    assert sum("Pipeline timeline for scope" in message for message in messages) == 1
    assert any("Build" in message and "runs=2" in message for message in messages)
    assert any("Total" in message and "0.500s" in message for message in messages)
