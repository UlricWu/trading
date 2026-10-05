# filepath: tests/training/steps/test_ic_evaluate.py
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pyarrow.parquet as pq
from sklearn.dummy import DummyRegressor

from src.access import meta
from src.training.artifact import save
from src.training.prediction import save_prediction_record
from src.training.context import TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.steps.ic_evaluate import ICEvaluateStep
from src.utils.path import PathManager


def _context(pm: PathManager, labels: list[float]) -> TrainingContext:
    day = "2026-07-03"
    ref = pm.experiment_training_model(experiment_name="run", window_index=0)
    keys = {
        "symbol": ["000001", "000002", "000003"],
        "trade_date": [day] * 3,
        "decision_ts_utc": [100] * 3,
    }
    model = InferenceModel(
        model=DummyRegressor().fit(np.array([[0.0], [1.0]]), np.array([0.0, 1.0])),
        preprocess=FittedPreprocessor(
            feature_names=("f",), missing_method="constant", fill_values=(0.0,),
        ),
        feature_set="l2_stock_1430", feature_version="v1", train_dates=("2026-07-01",),
        label_set="l2_stock_1430_t1_vwap_rank", label_version="v1", label_column="y_rank_return",
    )
    path = pm.label_object(
        label_set=model.label_set, version=model.label_version, trade_date=day,
    ).payload_path
    path.parent.mkdir(parents=True)
    pd.DataFrame({**keys, model.label_column: labels}).to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)
    save(ref, model)
    prediction_ref = ref.with_name("predictions.parquet")
    save_prediction_record(
        prediction_ref, model_ref=ref, trade_date=day, decision_ts_us=100,
        features=pd.DataFrame({**keys, "f": [0.0, np.nan, 2.0]}),
        feature_names=model.feature_names,
        predictions=np.array([0.0, np.nan, 2.0], dtype=np.float64),
    )
    return TrainingContext(window=TrainingWindow(
        train_dates=model.train_dates, model_ref=ref, eval_date=day,
        decision_ts_us=100, prediction_ref=prediction_ref,
    ))


def test_evaluation_never_predicts_or_filters_original_prediction_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [np.nan, 1.0, 2.0])
    original = context.window.prediction_ref.read_bytes()

    def fail_predict(self: InferenceModel, raw_values: np.ndarray) -> np.ndarray:
        raise AssertionError("evaluation must use fixed predictions")

    monkeypatch.setattr(InferenceModel, "predict", fail_predict)
    ICEvaluateStep(pm=pm).run(context)
    item = context.observations[0]
    assert (
        item.input_count, item.no_prediction_count, item.missing_label_count, item.pair_count,
    ) == (3, 1, 1, 1)
    assert item.undefined_reasons == ("insufficient_pairs", "constant_prediction", "constant_label")
    assert context.window.prediction_ref.read_bytes() == original
    # Later label changes alter evaluation, never the already fixed predictions.
    path = pm.label_object(
        label_set="l2_stock_1430_t1_vwap_rank", version="v1",
        trade_date=context.window.eval_date,
    ).payload_path
    changed = pd.read_parquet(path)
    changed["y_rank_return"] = [np.nan] * 3
    changed.to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)
    ICEvaluateStep(pm=pm).run(context)
    assert context.observations[-1].pair_count == 0
    assert context.window.prediction_ref.read_bytes() == original


def test_equal_symbol_date_with_wrong_third_key_is_rejected(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [0.0, 1.0, 2.0])
    prediction_ref = context.window.prediction_ref
    with pq.ParquetFile(prediction_ref) as source:
        table = source.read()
    table = table.set_column(2, "decision_ts_utc", [[101] * 3])
    pq.write_table(table, prediction_ref)
    with pytest.raises(RuntimeError, match="key mismatch"):
        ICEvaluateStep(pm=pm).run(context)


def test_infinite_label_is_an_error_even_on_a_row_without_prediction(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [0.0, np.inf, 2.0])
    with pytest.raises(ValueError, match="infinity"):
        ICEvaluateStep(pm=pm).run(context)
    assert context.observations == []
