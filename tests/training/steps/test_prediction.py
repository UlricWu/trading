# filepath: tests/training/steps/test_prediction.py
from datetime import date, time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sklearn.dummy import DummyRegressor

from src.access import meta
from src.training.artifact import save
from src.training.context import EvaluationDay, TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.steps.prediction import PredictionStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


def _context(pm: PathManager, values: list[float]) -> TrainingContext:
    day = "2026-07-03"
    decision = DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date.fromisoformat(day))
    feature = pm.feature_object(
        feature_set="l2_stock_1430", version="v1", trade_date=day,
    ).payload_path
    feature.parent.mkdir(parents=True)
    pd.DataFrame({
        "symbol": [f"{index:06d}" for index in range(len(values))],
        "trade_date": [day] * len(values),
        "decision_ts_utc": [decision] * len(values),
        "f_l2_notional_rank_5m": values,
    }).to_parquet(feature, index=False)
    meta.commit(pm=pm, payload_path=feature)
    ref = pm.experiment_training_model(experiment_name="run")
    model = InferenceModel(
        model=DummyRegressor(strategy="constant", constant=0.0).fit(
            np.array([[0.2], [0.8]]), np.array([0.25, 0.75]),
        ),
        preprocess=FittedPreprocessor(
            feature_names=("f_l2_notional_rank_5m",), missing_method="drop",
        ),
        feature_set="l2_stock_1430", feature_version="v1", train_dates=("2026-07-01",),
        label_set="l2_stock_1430_t1_vwap_rank", label_version="v1", label_column="y_rank_return",
    )
    save(ref, model)
    return TrainingContext(window=TrainingWindow(
        train_dates=model.train_dates, model_ref=ref,
        evaluations=(EvaluationDay(
            trade_date=day, decision_ts_us=decision,
            prediction_ref=ref.with_name("predictions.parquet"),
        ),),
    ))


def test_predictions_preserve_three_keys_all_rows_nulls_and_real_zero_without_labels(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [0.2, np.nan, 0.8])
    assert PredictionStep(pm=pm, clock=lambda: 0.0).run(context) is context
    assert context.model is not None
    output = context.window.model_ref.parent / "predictions.parquet"
    with pq.ParquetFile(output) as source:
        table = source.read()
    assert table.column_names == [
        "symbol", "trade_date", "decision_ts_utc", "decision_ts_us", "model_ref", "prediction",
    ]
    assert table.num_rows == 3
    assert table.schema.field("prediction").type == pa.float64()
    assert table.schema.field("decision_ts_us").type == pa.int64()
    assert table["prediction"].to_pylist() == [0.0, None, 0.0]
    assert table["prediction"].null_count == 1
    assert not list((tmp_path / "labels").iterdir())


def test_invalid_infinite_feature_fails_at_model_boundary(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [np.inf])
    with pytest.raises(ValueError, match="infinity"):
        PredictionStep(pm=pm, clock=lambda: 0.0).run(context)
    assert context.window.model_ref.is_file()
    assert not (context.window.model_ref.parent / "predictions.parquet").exists()


def test_requested_decision_must_match_the_preserved_feature_key(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [0.2])
    day = context.window.evaluations[0]
    context.window = replace(context.window, evaluations=(replace(day, decision_ts_us=day.decision_ts_us + 1),))
    with pytest.raises(ValueError, match="decision time"):
        PredictionStep(pm=pm, clock=lambda: 0.0).run(context)


@pytest.mark.parametrize("ticks,progress_count", [
    ((0.0, 0.1, 0.2, 0.3, 0.4, 0.5), 0),
    ((0.0, 4.99, 5.0, 9.99, 10.0, 99.0), 2),
])
def test_batch_prediction_logs_do_not_grow_per_day_or_repeat_full_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ticks: tuple[float, ...], progress_count: int,
) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm, [0.2, np.nan, 0.8])
    dates = ("2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09")
    days = tuple(EvaluationDay(
        trade_date=day, decision_ts_us=DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date.fromisoformat(day)),
        prediction_ref=context.window.model_ref.parent / f"{day}.parquet",
    ) for day in dates)
    context.window = replace(context.window, evaluations=days)
    frames = [pd.DataFrame({
        "symbol": ["000000", "000001", "000002"], "trade_date": [day.trade_date] * 3,
        "decision_ts_utc": [day.decision_ts_us] * 3, "f_l2_notional_rank_5m": [0.2, np.nan, 0.8],
    }) for day in days]
    monkeypatch.setattr("src.training.steps.prediction.read_features", Mock(side_effect=frames))
    logger = Mock()
    monkeypatch.setattr("src.training.steps.prediction.logs", logger)
    assert PredictionStep(pm=pm, clock=Mock(side_effect=ticks)).run(context) is context
    messages = [call.args[0] for call in logger.info.call_args_list]
    progress = [message for message in messages if message.startswith("⏳")]
    assert len(messages) == 2 + progress_count
    assert len(progress) == progress_count
    if progress:
        assert "已写入 2 / 5 个交易日；累计 6 行" in progress[0]
        assert "已写入 4 / 5 个交易日；累计 12 行" in progress[1]
    assert messages[0].startswith("▶️ 批量预测;") and messages[-1].startswith("✅ 批量预测;")
    assert "共 15 行（保留无预测行）" in messages[-1]
    assert str(context.window.model_ref) not in "\n".join(messages)
    for day in days:
        rows = pd.read_parquet(day.prediction_ref)
        assert len(rows) == 3 and rows["prediction"].isna().sum() == 1
