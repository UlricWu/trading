# filepath: tests/workflows/test_offline_training.py
"""Scheduling and assembly tests for the offline training workflow."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from src.config.model_config import ModelConfig
from src.utils.path import PathManager
from src.workflows import offline_training as training_workflow
from src.workflows.offline_training import (
    build_training_experiment_name,
    build_training_schedule,
)


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


def test_training_schedule_uses_rolling_window_and_forward_eval_offset() -> None:
    dates = (
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    )

    schedule = build_training_schedule(
        path_manager=cast("PathManager", object()),
        start_date=dates[0],
        end_date=dates[-1],
        train_window_days=2,
        eval_offset=1,
        calendar_fn=_FixedCalendar(dates),
    )

    assert [
        (
            entry.train_start_date,
            entry.train_end_date,
            entry.eval_start_date,
            entry.eval_end_date,
        )
        for entry in schedule
    ] == [
        ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-03"),
        ("2026-07-02", "2026-07-03", "2026-07-04", "2026-07-04"),
    ]


@pytest.mark.parametrize(
    ("open_dates", "expected_error"),
    [
        (
            ("2026-07-01", "2026-07-01", "2026-07-03", "2026-07-04"),
            "duplicate open_date",
        ),
        (
            ("2026-07-02", "2026-07-01", "2026-07-03", "2026-07-04"),
            "sorted ascending",
        ),
        (
            ("2026-06-30", "2026-07-01", "2026-07-03", "2026-07-04"),
            "out of requested range",
        ),
    ],
)
def test_training_schedule_rejects_ambiguous_calendar_results(
    open_dates: tuple[str, ...],
    expected_error: str,
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        build_training_schedule(
            path_manager=cast("PathManager", object()),
            start_date="2026-07-01",
            end_date="2026-07-04",
            train_window_days=1,
            eval_offset=1,
            calendar_fn=_FixedCalendar(open_dates),
        )


def test_training_experiment_name_preserves_the_accepted_job_identity() -> None:
    assert (
        build_training_experiment_name(
            start_date="2026-07-01",
            end_date="2026-07-20",
            experiment_id="run-1",
        )
        == "training_2026-07-01_2026-07-20_run-1"
    )


def test_training_rejects_an_existing_experiment_before_execution() -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.experiment_dir.return_value.exists.return_value = True

    with pytest.raises(FileExistsError, match="experiment already exists"):
        training_workflow.run_offline_training(
            model_config=cast("ModelConfig", object()),
            path_manager=path_manager,
            experiment_id="run-1",
            start_date="2026-07-01",
            end_date="2026-07-20",
        )


def test_training_workflow_assembles_owned_step_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.experiment_dir.return_value.exists.return_value = False
    dataset_config = SimpleNamespace(
        label_set="labels",
        label_version="v1",
        label_column="target",
    )
    model_config = SimpleNamespace(
        dataset=dataset_config,
        preprocessing=object(),
        model_params={"alpha": 0.1},
        train_window_days=30,
    )
    label_builder = Mock()
    label_builder.target_lookahead.return_value = 1
    monkeypatch.setattr(
        training_workflow,
        "get_label_builder",
        Mock(return_value=label_builder),
    )
    monkeypatch.setattr(
        training_workflow,
        "build_training_schedule",
        Mock(return_value=[object()]),
    )
    step_names = (
        "DatasetBuildStep",
        "PreprocessStep",
        "ModelTrainStep",
        "ICEvaluateStep",
        "ArtifactPersistStep",
        "ReportStep",
    )
    step_outputs = {name: object() for name in step_names}
    step_factories = {
        name: Mock(return_value=step_outputs[name]) for name in step_names
    }
    for step_name, step_factory in step_factories.items():
        monkeypatch.setattr(
            training_workflow,
            step_name,
            step_factory,
        )
    pipeline_result = object()
    pipeline = Mock()
    pipeline.run.return_value = pipeline_result
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(training_workflow, "TrainingPipeline", pipeline_factory)

    result = training_workflow.run_offline_training(
        model_config=cast("ModelConfig", model_config),
        path_manager=path_manager,
        experiment_id="run-1",
        start_date="2026-07-01",
        end_date="2026-07-20",
    )

    assert result is pipeline_result
    pipeline_arguments = pipeline_factory.call_args.kwargs
    assert pipeline_arguments["daily_steps"] == [
        step_outputs["DatasetBuildStep"],
        step_outputs["PreprocessStep"],
        step_outputs["ModelTrainStep"],
        step_outputs["ICEvaluateStep"],
    ]
    assert pipeline_arguments["final_steps"] == [
        step_outputs["ArtifactPersistStep"],
        step_outputs["ReportStep"],
    ]
    step_factories["ModelTrainStep"].assert_called_once_with(
        group="sgd_regression",
        model_params={"alpha": 0.1},
    )
    artifact_arguments = step_factories["ArtifactPersistStep"].call_args.kwargs
    assert artifact_arguments["experiment_id"] == "run-1"
    assert artifact_arguments["model_group"] == "sgd_regression"
    assert artifact_arguments["dataset_cfg"] is dataset_config
    assert artifact_arguments["label_lookahead"] == 1
    pipeline.run.assert_called_once()
    path_manager.experiment_dir.assert_called_once_with(
        experiment_name="training_2026-07-01_2026-07-20_run-1"
    )
