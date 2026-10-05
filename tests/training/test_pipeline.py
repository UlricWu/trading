# filepath: tests/training/test_pipeline.py
from collections.abc import Callable
from pathlib import Path

from src.pipeline import PipelineStep

import numpy as np
import pandas as pd
import pytest

from src.access import meta
from src.config.model_config import FeatureLabelConfig, MissingConfig, PreprocessingConfig
from src.observability.instrumentation import Instrumentation
from src.training.artifact import load
from src.training.context import TrainingContext, TrainingWindow
from src.training.evaluation import load_evaluation
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
from src.utils.path import PathManager


def _dataset() -> FeatureLabelConfig:
    return FeatureLabelConfig(feature_set="daily", feature_version="v1", label_set="rank", label_version="v1", feature_columns=["f"], label_column="target")


def _publish_inputs(pm: PathManager, day: str, features: list[float], labels: list[float] | None) -> None:
    keys = {"symbol": [f"{index:06d}" for index in range(len(features))], "trade_date": [day] * len(features)}
    feature = pm.feature_object(feature_set="daily", version="v1", trade_date=day).payload_path
    feature.parent.mkdir(parents=True)
    pd.DataFrame({**keys, "f": features}).to_parquet(feature, index=False)
    meta.commit(pm=pm, payload_path=feature)
    if labels is not None:
        label = pm.label_object(label_set="rank", version="v1", trade_date=day).payload_path
        label.parent.mkdir(parents=True)
        pd.DataFrame({**keys, "target": labels}).to_parquet(label, index=False)
        meta.commit(pm=pm, payload_path=label)


def _steps(pm: PathManager) -> tuple[PipelineStep[TrainingContext], ...]:
    return (
        DatasetBuildStep(pm=pm, dataset_cfg=_dataset()),
        PreprocessStep(PreprocessingConfig(missing=MissingConfig(method="drop"))),
        ModelTrainStep(trainer=get_model_trainer("sgd_regression"), model_params={"random_state": 7, "shuffle": False}, dataset_cfg=_dataset()),
        ArtifactPersistStep(), PredictionStep(pm=pm), ICEvaluateStep(pm=pm),
    )


def test_two_windows_deliver_independent_models_predictions_and_aggregate_report(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _publish_inputs(pm, "2026-07-01", [0.0, 1.0], [np.nan, np.nan])
    _publish_inputs(pm, "2026-07-02", [2.0, 4.0], [1.0, 2.0])
    _publish_inputs(pm, "2026-07-03", [0.0, np.nan, 2.0], [np.nan, 0.5, 1.0])
    _publish_inputs(pm, "2026-07-04", [np.nan, np.nan], [1.0, 2.0])
    windows = tuple(TrainingWindow(
        train_dates=("2026-07-01", "2026-07-02") + (("2026-07-03",) if index else ()),
        model_ref=pm.experiment_training_model(experiment_name="run", window_index=index),
        eval_date=f"2026-07-0{index+3}", decision_ts_us=100 + index,
        prediction_ref=pm.experiment_training_model(experiment_name="run", window_index=index).with_name("predictions.parquet"),
    ) for index in range(2))
    TrainingPipeline(
        windows=windows, per_window_steps=_steps(pm),
        final_steps=(MetricsPersistStep(pm=pm, experiment_name="run"), ReportStep(pm=pm, experiment_name="run", model_refs=tuple(window.model_ref for window in windows))),
        instrumentation=Instrumentation("run"),
    ).run()
    for window in windows:
        assert load(window.model_ref).train_dates == window.train_dates
        frame = pd.read_parquet(window.model_ref.parent / "predictions.parquet")
        assert frame["model_ref"].eq(str(window.model_ref)).all()
        assert frame["decision_ts_us"].eq(window.decision_ts_us).all()
    first = pd.read_parquet(windows[0].model_ref.parent / "predictions.parquet")
    assert len(first) == 3 and first["prediction"].isna().tolist() == [False, True, False]
    evaluation = load_evaluation(pm.experiment_training_metrics(experiment_name="run"))
    assert [item.input_count for item in evaluation.observations] == [3, 2]
    assert [item.pair_count for item in evaluation.observations] == [1, 0]
    assert evaluation.summary.date_count == 2 and evaluation.summary.mean_ic is None
    report = pm.experiment_training_report(experiment_name="run").read_text()
    assert str(windows[0].model_ref) in report and str(windows[1].model_ref) in report


def test_later_evaluation_failure_does_not_remove_delivered_model_or_prediction(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _publish_inputs(pm, "2026-07-01", [0.0, 1.0], [0.0, 1.0])
    _publish_inputs(pm, "2026-07-02", [2.0, 3.0], None)
    window = TrainingWindow(train_dates=("2026-07-01",), model_ref=pm.experiment_training_model(experiment_name="run", window_index=0), eval_date="2026-07-02", decision_ts_us=100, prediction_ref=pm.experiment_training_model(experiment_name="run", window_index=0).with_name("predictions.parquet"))
    with pytest.raises(FileNotFoundError):
        TrainingPipeline(windows=(window,), per_window_steps=_steps(pm), final_steps=(), instrumentation=Instrumentation("run")).run()
    assert load(window.model_ref).train_dates == window.train_dates
    assert len(pd.read_parquet(window.model_ref.parent / "predictions.parquet")) == 2
    assert not pm.experiment_training_metrics(experiment_name="run").exists()


def test_pipeline_has_fresh_contexts_shared_observations_and_preserves_returned_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[TrainingContext] = []
    measured: list[str] = []
    instrumentation = Instrumentation("run")
    original_measure = instrumentation.measure

    def measure(name: str, operation: Callable[[TrainingContext], TrainingContext], context: TrainingContext) -> TrainingContext:
        measured.append(name)
        return original_measure(name, operation, context)

    monkeypatch.setattr(instrumentation, "measure", measure)

    class First:
        def run(self, context: TrainingContext) -> TrainingContext:
            seen.append(context)
            assert context.train_X is None and context.model is None
            return TrainingContext(window=context.window, observations=context.observations, train_X=pd.DataFrame({"prediction": [1.0]}))

    class Second:
        def run(self, context: TrainingContext) -> TrainingContext:
            assert context.train_X is not None
            return context

    class Final:
        def run(self, context: TrainingContext) -> TrainingContext:
            assert context.observations is seen[0].observations
            return context

    windows = tuple(TrainingWindow(train_dates=("2026-07-01",), model_ref=tmp_path / str(index) / "model.joblib") for index in range(2))
    TrainingPipeline(windows=windows, per_window_steps=(First(), Second()), final_steps=(Final(),), instrumentation=instrumentation).run()
    assert seen[0] is not seen[1]
    assert seen[0].observations is seen[1].observations
    assert measured == ["First", "Second", "First", "Second", "Final"]
