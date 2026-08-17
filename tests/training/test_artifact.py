# filepath: tests/training/test_artifact.py
"""Public persistence and loading tests for training artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pytest

from src.training import artifact as artifact_module
from src.training.artifact import (
    TrainingParams,
    load_inference_model,
    load_training_report_inputs,
    persist_training_artifacts,
)
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.utils.path import PathManager

_EXPERIMENT = "training_2026-07-01_2026-07-20_run-1"


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


def _inference_model() -> InferenceModel:
    return InferenceModel(
        model=_FirstColumnModel(),
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="constant",
            fill_values=(0.0,),
        ),
        feature_set="daily",
        feature_version="v1",
    )


def _params() -> TrainingParams:
    return TrainingParams(
        experiment_id="run-1",
        model_group="sgd_regression",
        asof_day="2026-07-19",
        feature_set="daily",
        feature_version="v1",
        feature_names=("factor",),
        label_set="rank",
        label_version="v1",
        label_column="target",
        label_lookahead=1,
    )


def test_persist_and_load_publish_one_ready_inference_asset(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    inference_model = _inference_model()
    raw_values = np.array([[np.nan], [2.0]])
    expected_keep_rows, expected_predictions = inference_model.predict(raw_values)
    persist_training_artifacts(
        pm=pm,
        experiment_name=_EXPERIMENT,
        params=_params(),
        metrics={"ic@2026-07-20": 0.1},
        inference_model=inference_model,
    )

    training_dir = pm.experiment_training_dir(experiment_name=_EXPERIMENT)
    assert {path.name for path in training_dir.iterdir()} == {
        "params.json",
        "metrics.json",
        "inference.pkl",
    }
    loaded_model = load_inference_model(pm=pm, experiment_name=_EXPERIMENT)
    keep_rows, predictions = loaded_model.predict(raw_values)
    loaded_params, loaded_metrics = load_training_report_inputs(
        pm=pm,
        experiment_name=_EXPERIMENT,
    )
    assert keep_rows.tolist() == expected_keep_rows.tolist()
    assert predictions.tolist() == expected_predictions.tolist()
    assert loaded_params == _params()
    assert loaded_metrics == {"ic@2026-07-20": 0.1}
    params_payload = json.loads(
        pm.experiment_training_params(experiment_name=_EXPERIMENT).read_text()
    )
    assert set(params_payload) == {
        "experiment_id",
        "model_group",
        "asof_day",
        "feature_set",
        "feature_version",
        "feature_names",
        "label_set",
        "label_version",
        "label_column",
        "label_lookahead",
    }


def test_inference_is_published_after_report_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)

    def fail_dump(value: object, path: Path) -> None:
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(artifact_module.joblib, "dump", fail_dump)

    with pytest.raises(RuntimeError, match="serialization failed"):
        persist_training_artifacts(
            pm=pm,
            experiment_name=_EXPERIMENT,
            params=_params(),
            metrics={"ic@2026-07-20": 0.1},
            inference_model=_inference_model(),
        )

    assert pm.experiment_training_params(experiment_name=_EXPERIMENT).is_file()
    assert pm.experiment_training_metrics(experiment_name=_EXPERIMENT).is_file()
    assert not pm.experiment_training_inference(experiment_name=_EXPERIMENT).exists()


def test_load_inference_distinguishes_missing_corrupt_and_wrong_type(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    inference_path = pm.experiment_training_inference(experiment_name=_EXPERIMENT)

    with pytest.raises(FileNotFoundError):
        load_inference_model(pm=pm, experiment_name=_EXPERIMENT)

    inference_path.parent.mkdir(parents=True)
    inference_path.write_bytes(b"not a joblib artifact")
    with pytest.raises(ValueError, match="invalid training inference asset"):
        load_inference_model(pm=pm, experiment_name=_EXPERIMENT)

    joblib.dump({"model": "wrong"}, inference_path)
    with pytest.raises(TypeError, match="InferenceModel"):
        load_inference_model(pm=pm, experiment_name=_EXPERIMENT)


def test_report_loader_rejects_params_fields_outside_the_exact_schema(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    persist_training_artifacts(
        pm=pm,
        experiment_name=_EXPERIMENT,
        params=_params(),
        metrics={"ic@2026-07-20": 0.1},
        inference_model=_inference_model(),
    )
    params_path = pm.experiment_training_params(experiment_name=_EXPERIMENT)
    payload = json.loads(params_path.read_text())
    payload["created_at"] = "2026-07-20T12:00:00"
    params_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="fields must match schema"):
        load_training_report_inputs(pm=pm, experiment_name=_EXPERIMENT)


def test_report_loader_distinguishes_missing_and_corrupt_json(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    params_path = pm.experiment_training_params(experiment_name=_EXPERIMENT)

    with pytest.raises(FileNotFoundError):
        load_training_report_inputs(pm=pm, experiment_name=_EXPERIMENT)

    params_path.parent.mkdir(parents=True)
    params_path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid training params JSON"):
        load_training_report_inputs(pm=pm, experiment_name=_EXPERIMENT)

    params_path.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        load_training_report_inputs(pm=pm, experiment_name=_EXPERIMENT)


@pytest.mark.parametrize(
    "metrics",
    [{"loss": 0.1}, {"ic@2026-02-30": 0.1}, {"ic@2026-07-20": np.nan}],
)
def test_persist_rejects_metrics_outside_the_exact_schema(
    tmp_path: Path,
    metrics: dict[str, float],
) -> None:
    pm = PathManager(tmp_path)

    with pytest.raises(ValueError):
        persist_training_artifacts(
            pm=pm,
            experiment_name=_EXPERIMENT,
            params=_params(),
            metrics=metrics,
            inference_model=_inference_model(),
        )
