# filepath: tests/training/steps/test_artifact_persist.py
from pathlib import Path

import numpy as np
from sklearn.linear_model import SGDRegressor

from src.training.artifact import load
from src.training.context import TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.steps.artifact_persist import ArtifactPersistStep


def test_each_window_is_delivered_without_evaluation_or_metrics(tmp_path: Path) -> None:
    refs = []
    for index in range(2):
        ref = tmp_path / str(index) / "model.joblib"
        window = TrainingWindow(train_dates=(f"2026-07-0{index+1}",), model_ref=ref)
        model = InferenceModel(
            model=SGDRegressor(random_state=index).fit(np.array([[0.0], [1.0]]), np.array([0.0, 1.0])),
            preprocess=FittedPreprocessor(feature_names=("f",), missing_method="constant", fill_values=(0.0,)),
            feature_set="daily", feature_version="v1", train_dates=window.train_dates,
            label_set="rank", label_version="v1", label_column="target",
        )
        context = TrainingContext(window=window, model=model)
        assert ArtifactPersistStep().run(context) is context
        assert context.observations == []
        assert load(ref).train_dates == window.train_dates
        refs.append(ref)
    assert refs[0].is_file() and refs[1].is_file()
