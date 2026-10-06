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
from src.training.context import EvaluationDay, TrainingContext, TrainingWindow
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
        DatasetBuildStep(pm=pm, dataset_cfg=_dataset(), clock=lambda: 0.0),
        PreprocessStep(PreprocessingConfig(missing=MissingConfig(method="drop"))),
        ModelTrainStep(trainer=get_model_trainer("sgd_regression"), model_params={"random_state": 7, "shuffle": False}, dataset_cfg=_dataset()),
        ArtifactPersistStep(), PredictionStep(pm=pm, clock=lambda: 0.0), ICEvaluateStep(pm=pm, clock=lambda: 0.0),
    )


def test_one_model_preserves_all_evaluation_days_and_undefined_observations(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _publish_inputs(pm, "2026-07-01", [0.0, 1.0], [np.nan, np.nan])
    _publish_inputs(pm, "2026-07-02", [2.0, 4.0], [1.0, 2.0])
    _publish_inputs(pm, "2026-07-03", [0.0, np.nan, 2.0], [np.nan, 0.5, 1.0])
    _publish_inputs(pm, "2026-07-04", [np.nan, np.nan], [1.0, 2.0])
    ref = pm.experiment_training_model(experiment_name="run")
    days = tuple(EvaluationDay(
        trade_date=f"2026-07-0{index+3}", decision_ts_us=100 + index,
        prediction_ref=ref.parent / "predictions" / f"2026-07-0{index+3}.parquet",
    ) for index in range(2))
    window = TrainingWindow(train_dates=("2026-07-01", "2026-07-02"), model_ref=ref, evaluations=days)
    TrainingPipeline(
        window=window, steps=(*_steps(pm), MetricsPersistStep(pm=pm, experiment_name="run"),
                             ReportStep(pm=pm, experiment_name="run")),
        instrumentation=Instrumentation("run"),
    ).run()
    assert load(ref).train_dates == window.train_dates
    assert list(ref.parent.rglob("*.joblib")) == [ref]
    for day in days:
        frame = pd.read_parquet(day.prediction_ref)
        assert frame["model_ref"].eq(str(ref)).all()
        assert frame["decision_ts_us"].eq(day.decision_ts_us).all()
    first = pd.read_parquet(days[0].prediction_ref)
    assert len(first) == 3 and first["prediction"].isna().tolist() == [False, True, False]
    evaluation = load_evaluation(pm.experiment_training_metrics(experiment_name="run"))
    assert [item.input_count for item in evaluation.observations] == [3, 2]
    assert [item.pair_count for item in evaluation.observations] == [1, 0]
    assert evaluation.summary.date_count == 2 and evaluation.summary.mean_ic is None
    assert str(ref) in pm.experiment_training_report(experiment_name="run").read_text()


def test_later_evaluation_failure_does_not_remove_delivered_model_or_prediction(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _publish_inputs(pm, "2026-07-01", [0.0, 1.0], [0.0, 1.0])
    _publish_inputs(pm, "2026-07-02", [2.0, 3.0], None)
    ref = pm.experiment_training_model(experiment_name="run")
    window = TrainingWindow(train_dates=("2026-07-01",), model_ref=ref,
                            evaluations=(EvaluationDay(trade_date="2026-07-02", decision_ts_us=100,
                                                       prediction_ref=ref.with_name("predictions.parquet")),))
    with pytest.raises(FileNotFoundError):
        TrainingPipeline(window=window, steps=_steps(pm), instrumentation=Instrumentation("run")).run()
    assert load(window.model_ref).train_dates == window.train_dates
    assert len(pd.read_parquet(window.model_ref.parent / "predictions.parquet")) == 2
    assert not pm.experiment_training_metrics(experiment_name="run").exists()


def test_pipeline_preserves_returned_context_measurement_and_isolates_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            assert context.observations is seen[-1].observations
            return context

    for index in range(2):
        window = TrainingWindow(train_dates=("2026-07-01",), model_ref=tmp_path / str(index) / "model.joblib")
        TrainingPipeline(window=window, steps=(First(), Second(), Final()), instrumentation=instrumentation).run()
    assert seen[0] is not seen[1]
    assert seen[0].observations is not seen[1].observations
    assert measured == ["First", "Second", "Final", "First", "Second", "Final"]
