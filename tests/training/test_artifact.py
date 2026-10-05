# filepath: tests/training/test_artifact.py
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import joblib
import numpy as np
import pandas as pd
import pytest

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training import artifact
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.models.sgd_regression import train_sgd_regression


def _train_model() -> InferenceModel:
    raw = pd.DataFrame({"factor": [0.0, np.nan, 2.0, 3.0]})
    fitted = FittedPreprocessor.fit(train_X=raw, config=PreprocessingConfig(missing=MissingConfig(method="drop")))
    keep, values = fitted.transform(raw.to_numpy(dtype=np.float64))
    predictor = train_sgd_regression(
        X=pd.DataFrame(values, columns=fitted.feature_names),
        y=pd.Series([0.0, 0.5, 1.0, 1.5]).iloc[np.flatnonzero(keep)],
        model_params={"random_state": 7, "alpha": 0.001, "shuffle": False},
    )
    return InferenceModel(
        model=predictor, preprocess=fitted, feature_set="tushare_daily_basic", feature_version="v1",
        train_dates=("2026-07-01", "2026-07-02"), label_set="daily_close_return_rank_d1",
        label_version="v1", label_column="y_rank_return",
    )


def test_actual_sgd_single_file_roundtrip_is_exact(tmp_path: Path) -> None:
    model = _train_model()
    ref = tmp_path / "nested" / "model.joblib"
    inputs = np.array([[0.0], [np.nan], [2.0], [4.0]], dtype=np.float64)
    expected = model.predict(inputs)
    assert artifact.save(ref, model) is None
    restored = artifact.load(ref)
    np.testing.assert_array_equal(restored.predict(inputs), expected, strict=True)
    assert restored.train_dates == model.train_dates
    assert restored.label_set == model.label_set
    assert restored.label_version == model.label_version
    assert restored.label_column == model.label_column
    assert restored.preprocess == model.preprocess
    assert set(ref.parent.iterdir()) == {ref}


def test_existing_ref_is_never_replaced(tmp_path: Path) -> None:
    ref = tmp_path / "model.joblib"
    ref.write_bytes(b"already-published")
    with pytest.raises(FileExistsError):
        artifact.save(ref, _train_model())
    assert ref.read_bytes() == b"already-published"
    assert list(tmp_path.iterdir()) == [ref]


def test_concurrent_publish_has_exactly_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = Barrier(2)
    original = artifact.joblib.dump

    def synchronized_dump(model: object, stream: object) -> object:
        result = original(model, stream)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(artifact.joblib, "dump", synchronized_dump)
    ref = tmp_path / "model.joblib"
    model = _train_model()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(artifact.save, ref, model) for _ in range(2)]
        errors = [future.exception(timeout=15) for future in futures]
    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, FileExistsError) for error in errors) == 1
    np.testing.assert_array_equal(artifact.load(ref).predict(np.array([[1.0]])), model.predict(np.array([[1.0]])), strict=True)
    assert list(tmp_path.iterdir()) == [ref]


@pytest.mark.parametrize("operation", ["serialization", "publication"])
def test_failure_propagates_original_error_and_removes_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    error = OSError("injected failure")

    def fail(*args: object, **kwargs: object) -> None:
        raise error

    if operation == "serialization":
        monkeypatch.setattr(artifact.joblib, "dump", fail)
    else:
        monkeypatch.setattr(artifact.os, "link", fail)
    with pytest.raises(OSError) as caught:
        artifact.save(tmp_path / "model.joblib", _train_model())
    assert caught.value is error
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_after_publication_keeps_complete_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = _train_model()
    ref = tmp_path / "model.joblib"
    error = PermissionError("injected cleanup failure")

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(PermissionError) as caught:
            artifact.save(ref, model)
        assert caught.value is error
    np.testing.assert_array_equal(artifact.load(ref).predict(np.array([[1.0]])), model.predict(np.array([[1.0]])), strict=True)
    assert ref.is_file()
    assert len(list(tmp_path.iterdir())) == 2


def test_loader_rejects_wrong_type_without_revalidating_model_sources(tmp_path: Path) -> None:
    ref = tmp_path / "wrong.joblib"
    joblib.dump({"model": "wrong"}, ref)
    with pytest.raises(TypeError, match="InferenceModel"):
        artifact.load(ref)
    with pytest.raises(FileNotFoundError):
        artifact.load(tmp_path / "missing.joblib")


def test_loader_preserves_original_restoration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error = ImportError("missing model dependency")

    def fail(ref: Path) -> None:
        raise error

    monkeypatch.setattr(artifact.joblib, "load", fail)
    with pytest.raises(ImportError) as caught:
        artifact.load(tmp_path / "model.joblib")
    assert caught.value is error


def test_relative_refs_are_rejected_before_io() -> None:
    with pytest.raises(ValueError, match="absolute"):
        artifact.save(Path("relative/model.joblib"), _train_model())
    with pytest.raises(ValueError, match="absolute"):
        artifact.load(Path("relative/model.joblib"))
