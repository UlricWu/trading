# filepath: tests/workflows/test_backtest.py
"""Scheduling and assembly tests for the backtest workflow."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from src.config.backtest_config import BacktestConfig, BacktestMode
from src.utils.path import PathManager
from src.workflows import backtest as backtest_workflow


@dataclass(frozen=True, slots=True)
class _FixedCalendar:
    open_dates: tuple[str, ...]

    def __call__(
        self,
        *,
        pm: PathManager,
        start_date: str,
        end_date: str,
    ) -> tuple[str, ...]:
        assert pm is not None
        assert start_date == "2026-07-01"
        assert end_date == "2026-07-04"
        return self.open_dates


def test_backtest_experiment_name_preserves_the_accepted_job_identity() -> None:
    assert (
        backtest_workflow.build_backtest_experiment_name(
            start_date="2026-07-01",
            end_date="2026-07-20",
            experiment_id="run-1",
        )
        == "backtest_2026-07-01_2026-07-20_run-1"
    )


def test_backtest_rejects_an_existing_experiment_before_execution() -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.experiment_dir.return_value.exists.return_value = True

    with pytest.raises(FileExistsError, match="experiment already exists"):
        backtest_workflow.run_daily_alpha_backtest(
            backtest_config=cast("BacktestConfig", object()),
            path_manager=path_manager,
            experiment_id="run-1",
            start_date="2026-07-01",
            end_date="2026-07-20",
        )


def test_backtest_schedule_maps_adjacent_open_dates_to_forward_dates() -> None:
    dates = (
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    )

    schedule = backtest_workflow.build_backtest_schedule(
        path_manager=cast("PathManager", object()),
        start_date=dates[0],
        end_date=dates[-1],
        calendar_fn=_FixedCalendar(dates),
    )

    assert [
        (timing.signal_date, timing.feature_date, timing.forward_date)
        for timing in schedule
    ] == [
        ("2026-07-01", "2026-07-01", "2026-07-02"),
        ("2026-07-02", "2026-07-02", "2026-07-03"),
        ("2026-07-03", "2026-07-03", "2026-07-04"),
    ]


def test_backtest_uses_config_as_the_only_mode_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.experiment_dir.return_value.exists.return_value = False
    backtest_config = SimpleNamespace(
        backtest_mode=BacktestMode.RISK_EVAL,
        init_cash=200_000,
    )
    backtest_components = SimpleNamespace(
        signal=object(),
        feature_set="feature_set",
        feature_version="v1",
        feature_names=("feature",),
        constructor=object(),
        target_capacity=None,
        risk=object(),
        execution=object(),
    )
    build_components = Mock(return_value=backtest_components)
    pipeline_result = object()
    pipeline = Mock()
    pipeline.run.return_value = pipeline_result
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(
        backtest_workflow.sim_components, "build_components", build_components
    )
    monkeypatch.setattr(
        backtest_workflow,
        "build_backtest_schedule",
        Mock(return_value=[object()]),
    )
    monkeypatch.setattr(backtest_workflow, "TradingPipeline", pipeline_factory)
    monkeypatch.setattr(
        backtest_workflow,
        "TradingContext",
        Mock(return_value=object()),
    )

    result = backtest_workflow.run_daily_alpha_backtest(
        backtest_config=cast("BacktestConfig", backtest_config),
        path_manager=path_manager,
        experiment_id="run-1",
        start_date="2026-07-01",
        end_date="2026-07-20",
    )

    assert result is pipeline_result
    build_components.assert_called_once_with(
        mode=BacktestMode.RISK_EVAL,
        cfg=backtest_config,
        pm=path_manager,
    )
    pipeline.run.assert_called_once()
    path_manager.experiment_dir.assert_called_once_with(
        experiment_name="backtest_2026-07-01_2026-07-20_run-1"
    )
    pipeline_arguments = pipeline_factory.call_args.kwargs
    assert [type(step).__name__ for step in pipeline_arguments["per_timing_steps"]] == [
        "SignalStep",
        "SignalEvalStep",
        "TradableAlphaEvalStep",
        "PortfolioStep",
        "RiskEvalStep",
        "ExecutionEvalStep",
        "AccountingStep",
        "FullBacktestStep",
    ]
    assert [type(step).__name__ for step in pipeline_arguments["final_steps"]] == [
        "MetricsPersistStep",
        "ReportStep",
    ]
