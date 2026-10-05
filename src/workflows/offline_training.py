# filepath: src/workflows/offline_training.py
"""Compose explicit training deliveries and optional fixed-prediction evaluation."""

from __future__ import annotations

import json
from datetime import date, time

from src.access import Access
from src.config.model_config import ModelConfig
from src.data_system.builders.registry import get_feature_builder, get_label_builder
from src.jobs.requests import TrainingSubmission
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.training.context import TrainingContext, TrainingWindow
from src.training.models.catalog import get_model_trainer
from src.training.pipeline import TrainingPipeline
from src.training.steps.artifact_persist import ArtifactPersistStep
from src.training.steps.dataset_build import DatasetBuildStep
from src.training.steps.ic_evaluate import ICEvaluateStep
from src.training.steps.metrics_persist import MetricsPersistStep
from src.training.steps.model_train import ModelTrainStep
from src.training.steps.prediction import PredictionStep
from src.training.steps.preprocess import PreprocessStep
from src.training.steps.report import ReportStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION, require_new_experiment


def resolve_training_windows(
    *,
    submission: TrainingSubmission,
    access: Access,
    path_manager: PathManager,
    experiment_name: str,
    label_lookahead: int,
    label_maturity_time: time,
) -> tuple[TrainingWindow, ...]:
    """Check complete selections against calendar, maturity, and task availability.

    Dates violating the information cutoff fail instead of being shortened.
    Availability evidence is supplied by the task; Meta does not prove history.

    Example:
        windows = resolve_training_windows(
            submission=training_submission, access=access,
            path_manager=path_manager, experiment_name="training_run",
            label_lookahead=1, label_maturity_time=time(15, 0),
        )
    """
    open_dates = set(access.trade_dates(start_date=submission.start, end_date=submission.end))
    maturity_times: dict[str, int] = {}
    windows: list[TrainingWindow] = []
    for index, selection in enumerate(submission.windows):
        for train_date in selection.train_dates:
            if train_date not in open_dates:
                raise ValueError(f"selected training date is not an open session: {train_date}")
            if train_date not in maturity_times:
                mature_date = train_date
                for _ in range(label_lookahead):
                    mature_date = access.next_trade_date(trade_date=mature_date)
                maturity_times[train_date] = DateTimeUtils.local_time_to_utc_epoch_us(
                    label_maturity_time, date.fromisoformat(mature_date),
                )
            fact = selection.label_availability[train_date]
            if fact.available_ts_us < maturity_times[train_date]:
                raise ValueError(f"label availability precedes maturity: train_date={train_date}")
            if fact.available_ts_us > selection.training_cutoff_ts_us:
                raise ValueError(f"label is unavailable at training cutoff: train_date={train_date}")
        if selection.eval_date is not None:
            if selection.eval_date not in open_dates:
                raise ValueError(f"selected evaluation date is not an open session: {selection.eval_date}")
        model_ref = path_manager.experiment_training_model(
            experiment_name=experiment_name, window_index=index,
        )
        windows.append(
            TrainingWindow(
                train_dates=selection.train_dates,
                model_ref=model_ref,
                eval_date=selection.eval_date,
                decision_ts_us=selection.decision_ts_us,
                prediction_ref=(
                    model_ref.with_name("predictions.parquet")
                    if selection.eval_date is not None else None
                ),
            )
        )
    return tuple(windows)


def run_offline_training(
    *,
    model_config: ModelConfig,
    path_manager: PathManager,
    submission: TrainingSubmission,
    experiment_id: str,
) -> None:
    """Deliver every selected model, then optionally evaluate fixed predictions.

    Example:
        run_offline_training(
            model_config=model_config, path_manager=path_manager,
            submission=training_submission, experiment_id="run-1",
        )
    """
    trainer = get_model_trainer(model_config.group)
    dataset = model_config.dataset
    if (dataset.feature_set, dataset.feature_version) == ("l2_stock_1430", "v1"):
        decision_time = time(14, 30)
        exact_decision = True
    else:
        get_feature_builder(dataset.feature_set, dataset.feature_version)
        decision_time = time(15, 0)
        exact_decision = False
    if (dataset.label_set, dataset.label_version) == ("l2_stock_1430_t1_vwap_rank", "v1"):
        label_column = "y_rank_return"
        lookahead = 1
        maturity_time = time(14, 36)
    else:
        label_builder = get_label_builder(dataset.label_set, dataset.label_version)
        label_column = label_builder.label_column
        lookahead = label_builder.lookahead
        maturity_time = time(15, 0)
    if dataset.label_column != label_column:
        raise ValueError(f"label set {dataset.label_set!r} exposes only label_column={label_column!r}")
    for selection in submission.windows:
        if selection.eval_date is not None:
            minimum = DateTimeUtils.local_time_to_utc_epoch_us(
                decision_time, date.fromisoformat(selection.eval_date),
            )
            if (
                (exact_decision and selection.decision_ts_us != minimum)
                or selection.decision_ts_us < minimum
            ):
                raise ValueError("prediction timing violates the selected feature's visibility")
    experiment_name = require_new_experiment(
        path_manager=path_manager, kind="training", start_date=submission.start,
        end_date=submission.end, experiment_id=experiment_id,
    )
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    windows = resolve_training_windows(
        submission=submission, access=access, path_manager=path_manager,
        experiment_name=experiment_name, label_lookahead=lookahead,
        label_maturity_time=maturity_time,
    )
    per_window_steps: tuple[PipelineStep[TrainingContext], ...] = (
        DatasetBuildStep(pm=path_manager, dataset_cfg=dataset),
        PreprocessStep(model_config.preprocessing),
        ModelTrainStep(trainer=trainer, model_params=model_config.model_params, dataset_cfg=dataset),
        ArtifactPersistStep(),
    )
    final_steps: tuple[PipelineStep[TrainingContext], ...] = ()
    if any(window.eval_date is not None for window in windows):
        per_window_steps += (PredictionStep(pm=path_manager), ICEvaluateStep(pm=path_manager))
        final_steps = (
            MetricsPersistStep(pm=path_manager, experiment_name=experiment_name),
            ReportStep(
                pm=path_manager,
                experiment_name=experiment_name,
                model_refs=tuple(window.model_ref for window in windows),
            ),
        )
    schedule = [
        {**selection.model_dump(mode="json"), "model_ref": str(window.model_ref)}
        for selection, window in zip(submission.windows, windows, strict=True)
    ]
    FileSystem.write_bytes_atomic(
        path_manager.experiment_training_schedule(experiment_name=experiment_name),
        json.dumps(schedule, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
    )
    TrainingPipeline(
        windows=windows,
        per_window_steps=per_window_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    ).run()
