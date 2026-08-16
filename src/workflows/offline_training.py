# filepath: src/workflows/offline_training.py
"""Compose one offline-training execution."""

from __future__ import annotations

from collections.abc import Sequence

from src.access import Access
from src.config.model_config import ModelConfig
from src.data_system.builders.registry import get_label_builder
from src.jobs.requests import TrainingSubmission
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.training.context import TrainingContext, TrainingWindow
from src.training.models.catalog import get_model_trainer
from src.training.pipeline import TrainingPipeline
from src.training.steps.artifact_persist import ArtifactPersistStep
from src.training.steps.dataset_build import DatasetBuildStep
from src.training.steps.ic_evaluate import ICEvaluateStep
from src.training.steps.model_train import ModelTrainStep
from src.training.steps.preprocess import PreprocessStep
from src.training.steps.report import ReportStep
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION, require_new_experiment


def resolve_training_windows(
    *,
    open_dates: Sequence[str],
    train_window_days: int,
    eval_offset: int,
) -> tuple[TrainingWindow, ...]:
    """Resolve ordered open dates into expanding or rolling windows.

    Example:
        windows = resolve_training_windows(
            open_dates=(
                "2026-07-17",
                "2026-07-20",
                "2026-07-21",
            ),
            train_window_days=2,
            eval_offset=1,
        )
    """
    windows: list[TrainingWindow] = []
    dates = tuple(open_dates)
    for train_end_index in range(len(dates)):
        eval_index = train_end_index + eval_offset
        if eval_index >= len(dates):
            continue
        if train_window_days == 0:
            train_start_index = 0
        else:
            train_start_index = train_end_index - train_window_days + 1
            if train_start_index < 0:
                continue
        windows.append(
            TrainingWindow(
                train_dates=dates[train_start_index : train_end_index + 1],
                eval_date=dates[eval_index],
            )
        )
    if not windows:
        raise ValueError(
            "[TrainingSchedule] empty schedule; check date range, "
            "train_window_days, and eval_offset"
        )
    return tuple(windows)


def run_offline_training(
    *,
    model_config: ModelConfig,
    path_manager: PathManager,
    submission: TrainingSubmission,
    experiment_id: str,
) -> None:
    """Run one accepted range through the offline training workflow.

    Example:
        run_offline_training(
            model_config=model_config,
            path_manager=path_manager,
            submission=TrainingSubmission(
                start="2026-07-01",
                end="2026-07-20",
            ),
            experiment_id="run-1",
        )
    """
    trainer = get_model_trainer(model_config.group)
    experiment_name = require_new_experiment(
        path_manager=path_manager,
        kind="training",
        start_date=submission.start,
        end_date=submission.end,
        experiment_id=experiment_id,
    )
    label_builder = get_label_builder(
        model_config.dataset.label_set,
        model_config.dataset.label_version,
    )
    eval_offset = label_builder.target_lookahead(model_config.dataset.label_column)
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    open_dates = access.trade_dates(
        start_date=submission.start,
        end_date=submission.end,
    )
    windows = resolve_training_windows(
        open_dates=open_dates,
        train_window_days=model_config.train_window_days,
        eval_offset=eval_offset,
    )
    per_window_steps: tuple[PipelineStep[TrainingContext], ...] = (
        DatasetBuildStep(
            pm=path_manager,
            dataset_cfg=model_config.dataset,
            processed_version=PROCESSED_VERSION,
        ),
        PreprocessStep(model_config.preprocessing),
        ModelTrainStep(
            trainer=trainer,
            model_params=model_config.model_params,
        ),
        ICEvaluateStep(),
    )
    final_steps: tuple[PipelineStep[TrainingContext], ...] = (
        ArtifactPersistStep(
            pm=path_manager,
            experiment_name=experiment_name,
            experiment_id=experiment_id,
            model_group=model_config.group,
            dataset_cfg=model_config.dataset,
            label_lookahead=eval_offset,
            processed_version=PROCESSED_VERSION,
        ),
        ReportStep(
            pm=path_manager,
            experiment_name=experiment_name,
        ),
    )
    pipeline = TrainingPipeline(
        windows=windows,
        per_window_steps=per_window_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run()
