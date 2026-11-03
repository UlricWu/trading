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

    descriptions = {"Load": "数据准备"}
    with Instrumentation("scope", descriptions=descriptions) as instrumentation:
        descriptions["Load"] = "changed after composition"
        result = instrumentation.measure(
            "Load",
            operation,
            "2026-07-20",
            required=True,
        )

    assert result == {"status": "complete"}
    operation.assert_called_once_with("2026-07-20", required=True)
    logger.info.assert_called_once_with(
        "\n".join(
            (
                "✅ 步骤耗时; scope=scope",
                f"{'Load':<35} {0.25:>8.3f}s  数据准备",
                "步骤耗时合计：0.250s（各步骤累计，不代表任务总耗时）",
            )
        )
    )
    logger.error.assert_not_called()


def test_measure_reports_failed_timeline_and_preserves_the_exception(
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
    assert raised.value.__notes__ == ["Pipeline 执行; scope=scope step=Build"]
    logger.error.assert_called_once_with(
        "\n".join(
            (
                "❌ 步骤耗时; scope=scope",
                f"{'Build':<35} {0.5:>8.3f}s avg=0.250s runs=2",
                "步骤耗时合计：0.500s（各步骤累计，不代表任务总耗时）",
            )
        )
    )
    logger.info.assert_not_called()
