# filepath: tests/training/test_prediction.py
"""Persisted prediction identity and fresh-process replay acceptance."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.training.artifact import load, save
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.models.sgd_regression import train_sgd_regression
from src.training.prediction import read_prediction_record, save_prediction_record, verify_prediction_record
from src.utils.path import PathManager


@pytest.fixture
def recorded_prediction(tmp_path: Path) -> tuple[PathManager, Path, Path]:
    pm = PathManager(tmp_path)
    model = InferenceModel(
        model=train_sgd_regression(
            X=pd.DataFrame({"factor": [0.0, 1.0, 2.0]}),
            y=pd.Series([0.0, 0.5, 1.0]),
            model_params={"random_state": 7},
        ),
        preprocess=FittedPreprocessor(feature_names=("factor",), missing_method="drop"),
        feature_set="tushare_daily_basic", feature_version="v1",
        train_dates=("2026-07-01",),
        label_set="daily_close_return_rank_d1", label_version="v1", label_column="y_rank_return",
    )
    model_ref = tmp_path / "model.joblib"
    save(model_ref, model)
    prediction_refs = []
    for day in ("2026-07-03", "2026-07-06"):
        features = pd.DataFrame({
            "symbol": ["000001", "600000"], "trade_date": [day] * 2,
            "factor": [1.0, np.nan],
        })
        feature_ref = pm.feature_object(
            feature_set=model.feature_set, version=model.feature_version, trade_date=day,
        ).payload_path
        feature_ref.parent.mkdir(parents=True)
        features.to_parquet(feature_ref, index=False)
        meta.commit(pm=pm, payload_path=feature_ref)
        prediction_ref = tmp_path / "run" / f"{day}.parquet"
        save_prediction_record(
            prediction_ref, model_ref=model_ref, trade_date=day, decision_ts_us=100,
            features=features, feature_names=model.feature_names,
            predictions=model.predict(features[["factor"]].to_numpy()),
        )
        prediction_refs.append(prediction_ref)
    return pm, prediction_refs[0], prediction_refs[1]


def test_multiple_dates_survive_and_replay_in_a_fresh_process(
    recorded_prediction: tuple[PathManager, Path, Path], tmp_path: Path,
) -> None:
    pm, first_ref, second_ref = recorded_prediction
    first_bytes = first_ref.read_bytes()
    model_bytes = (tmp_path / "model.joblib").read_bytes()
    for ref in (first_ref, second_ref):
        verify_prediction_record(ref, pm=pm)
        result = subprocess.run(
            [sys.executable, "-c", "\n".join([
                "from pathlib import Path",
                "import sys",
                "from src.training.prediction import verify_prediction_record",
                "from src.utils.path import PathManager",
                "verify_prediction_record(Path(sys.argv[1]), pm=PathManager(Path(sys.argv[2])))",
            ]), str(ref), str(tmp_path)],
            check=False, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
    assert first_ref.read_bytes() == first_bytes
    assert (tmp_path / "model.joblib").read_bytes() == model_bytes
    assert pd.read_parquet(first_ref)["trade_date"].eq("2026-07-03").all()
    assert pd.read_parquet(second_ref)["trade_date"].eq("2026-07-06").all()


def test_prediction_identity_uses_model_and_decision_without_content_digests(
    recorded_prediction: tuple[PathManager, Path, Path], tmp_path: Path,
) -> None:
    pm, ref, _ = recorded_prediction
    inputs, rows = read_prediction_record(ref)
    assert inputs.model_dump() == {"model_ref": tmp_path / "model.joblib",
        "trade_date": "2026-07-03", "decision_ts_us": 100}
    assert rows["symbol"].tolist() == ["000001", "600000"]
    assert rows["prediction"].iloc[0] != 0 and np.isnan(rows["prediction"].iloc[1])
    verify_prediction_record(ref, pm=pm)


@pytest.mark.parametrize("changed", ["features", "prediction"])
def test_replay_rejects_different_predictions_without_overwriting_evidence(
    recorded_prediction: tuple[PathManager, Path, Path], changed: str,
) -> None:
    pm, ref, _ = recorded_prediction
    if changed == "features":
        feature_ref = pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date="2026-07-03",
        ).payload_path
        frame = pd.read_parquet(feature_ref)
        frame["factor"] = [2.0, np.nan]
        frame.to_parquet(feature_ref, index=False)
        meta.commit(pm=pm, payload_path=feature_ref)
    else:
        with pq.ParquetFile(ref) as source:
            table = source.read()
        table = table.set_column(table.column_names.index("prediction"), "prediction", [[99.0, None]])
        pq.write_table(table, ref)
    original = ref.read_bytes()
    with pytest.raises(AssertionError):
        verify_prediction_record(ref, pm=pm)
    assert ref.read_bytes() == original


def test_replay_requires_the_referenced_model(
    recorded_prediction: tuple[PathManager, Path, Path], tmp_path: Path,
) -> None:
    pm, ref, _ = recorded_prediction
    (tmp_path / "model.joblib").unlink()
    original = ref.read_bytes()
    with pytest.raises(FileNotFoundError):
        verify_prediction_record(ref, pm=pm)
    assert ref.read_bytes() == original


def test_empty_prediction_record_keeps_identity_and_can_be_replayed(
    recorded_prediction: tuple[PathManager, Path, Path], tmp_path: Path,
) -> None:
    pm, _, _ = recorded_prediction
    model_ref = tmp_path / "model.joblib"
    model = load(model_ref)
    feature_ref = pm.feature_object(
        feature_set=model.feature_set, version=model.feature_version, trade_date="2026-07-03",
    ).payload_path
    features = pd.read_parquet(feature_ref).iloc[:0]
    features.to_parquet(feature_ref, index=False)
    meta.commit(pm=pm, payload_path=feature_ref)
    ref = tmp_path / "empty.parquet"
    save_prediction_record(
        ref, model_ref=model_ref, trade_date="2026-07-03", decision_ts_us=100,
        features=features, feature_names=model.feature_names,
        predictions=model.predict(features[["factor"]].to_numpy()),
    )
    inputs, rows = read_prediction_record(ref)
    assert inputs.model_ref == model_ref
    assert inputs.decision_ts_us == 100
    assert rows.empty
    verify_prediction_record(ref, pm=pm)
