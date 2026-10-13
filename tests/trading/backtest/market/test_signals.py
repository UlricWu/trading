# filepath: tests/trading/backtest/market/test_signals.py
"""Signal preparation establishes trading bounds before returning replay inputs."""

import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from pytest_mock import MockerFixture
from sklearn.dummy import DummyRegressor

from src.access import Access, meta
from src.config.model_config import FeatureLabelConfig, ModelConfig
from src.jobs.requests import ModelSignalInput, RecordedSignalInput, SignalInput
from src.trading.backtest.market import signals
from src.training.artifact import load, save
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.prediction import save_prediction_record
from src.training.record import TrainingRunRecord
from src.utils.path import PathManager


FIRST_DECISION_TS_US = 1783060200000000
LAST_DECISION_TS_US = FIRST_DECISION_TS_US + 3 * 24 * 60 * 60 * 1_000_000


@pytest.fixture
def model_ref(tmp_path: Path) -> Path:
    model = InferenceModel(
        model=DummyRegressor().fit(np.array([[0.0], [1.0]]), np.array([0.0, 1.0])),
        preprocess=FittedPreprocessor(feature_names=("factor",), missing_method="drop"),
        feature_set="tushare_daily_basic", feature_version="v1", train_dates=("2026-07-01",),
        label_set="daily_close_return_rank_d1", label_version="v1", label_column="y_rank_return",
    )
    ref = tmp_path / "model.joblib"
    save(ref, model)
    record = TrainingRunRecord(
        request={}, train_dates=model.train_dates, eval_dates=(), model_ref=ref,
        label_maturity_ts_us=FIRST_DECISION_TS_US - 1,
        configuration=ModelConfig(group="sgd_regression", dataset=FeatureLabelConfig(
            feature_set=model.feature_set, feature_version=model.feature_version,
            feature_columns=list(model.feature_names), label_set=model.label_set,
            label_version=model.label_version, label_column=model.label_column,
        )),
    )
    ref.with_name("schedule.json").write_text(record.model_dump_json())
    return ref


@pytest.fixture
def prediction_refs(tmp_path: Path, model_ref: Path) -> tuple[Path, ...]:
    pm = PathManager(tmp_path)
    refs = []
    for day, ts_us in (("2026-07-03", FIRST_DECISION_TS_US), ("2026-07-06", LAST_DECISION_TS_US)):
        features = pd.DataFrame({
            "symbol": ["000001", "600000"], "trade_date": [day] * 2,
            "decision_ts_utc": [ts_us] * 2, "factor": [1.0, np.nan],
        })
        feature_ref = pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date=day,
        ).payload_path
        feature_ref.parent.mkdir(parents=True)
        features.to_parquet(feature_ref, index=False)
        meta.commit(pm=pm, payload_path=feature_ref)
        ref = tmp_path / f"{day}.parquet"
        save_prediction_record(ref, model_ref=model_ref, trade_date=day, decision_ts_us=ts_us,
            features=features, feature_names=("factor",), predictions=np.array([0.5, np.nan]))
        refs.append(ref)
    return tuple(refs)


@pytest.fixture
def access() -> Mock:
    access = Mock(spec=Access)
    access.trade_dates.side_effect = lambda *, start_date, end_date: [
        day for day in ("2026-07-03", "2026-07-06") if start_date <= day <= end_date
    ]
    access.universe.return_value = ("000001", "600000")
    return access


@pytest.fixture(params=("recorded", "model"))
def signal_input(request: pytest.FixtureRequest, model_ref: Path, prediction_refs: tuple[Path, ...]) -> SignalInput:
    if request.param == "recorded":
        return RecordedSignalInput(type="recorded", prediction_refs=tuple(reversed(prediction_refs)))
    return ModelSignalInput(type="model", model_ref=model_ref)


@pytest.mark.parametrize(("start_ts_us", "end_ts_us", "accepted"), [
    (FIRST_DECISION_TS_US, LAST_DECISION_TS_US + 1, True),
    (FIRST_DECISION_TS_US - 1, LAST_DECISION_TS_US + 1, True),
    (FIRST_DECISION_TS_US + 1, LAST_DECISION_TS_US + 1, False),
    (FIRST_DECISION_TS_US, LAST_DECISION_TS_US, False),
    (FIRST_DECISION_TS_US, LAST_DECISION_TS_US - 1, False),
])
def test_trading_preparation_enforces_half_open_simulation_interval(
    tmp_path: Path, signal_input: SignalInput, access: Mock,
    start_ts_us: int, end_ts_us: int, accepted: bool,
) -> None:
    if not accepted:
        with pytest.raises(ValueError, match="simulation interval"):
            signals.prepare_signals(signal_input, start="2026-07-03", end="2026-07-06",
                access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
                start_ts_us=start_ts_us, end_ts_us=end_ts_us)
        return
    signal_inputs_by_ts_us = signals.prepare_signals(signal_input, start="2026-07-03", end="2026-07-06",
        access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
        start_ts_us=start_ts_us, end_ts_us=end_ts_us)
    assert tuple(signal_inputs_by_ts_us) == (FIRST_DECISION_TS_US, LAST_DECISION_TS_US)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_trading_preparation_checks_maturity_at_earliest_selected_decision(
    tmp_path: Path, signal_input: SignalInput, access: Mock, model_ref: Path, offset: int,
) -> None:
    record_ref = model_ref.with_name("schedule.json")
    record = json.loads(record_ref.read_text())
    record["label_maturity_ts_us"] = FIRST_DECISION_TS_US + offset
    record_ref.write_text(json.dumps(record))
    if offset > 0:
        with pytest.raises(ValueError, match="label maturity"):
            signals.prepare_signals(signal_input, start="2026-07-03", end="2026-07-06",
                access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
                start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)
        return
    signal_inputs_by_ts_us = signals.prepare_signals(signal_input, start="2026-07-03", end="2026-07-06",
        access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
        start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)
    assert tuple(signal_inputs_by_ts_us) == (FIRST_DECISION_TS_US, LAST_DECISION_TS_US)


@pytest.mark.parametrize("training_record", ["missing", "immature"])
def test_fixed_signals_require_a_mature_training_record(
    tmp_path: Path, model_ref: Path, prediction_refs: tuple[Path, ...], access: Mock, training_record: str,
) -> None:
    record_ref = model_ref.with_name("schedule.json")
    if training_record == "missing":
        record_ref.unlink()
        expected_error = FileNotFoundError
    else:
        record = json.loads(record_ref.read_text())
        record["label_maturity_ts_us"] = LAST_DECISION_TS_US + 1
        record_ref.write_text(json.dumps(record))
        expected_error = ValueError
    signal = RecordedSignalInput(type="recorded", prediction_refs=prediction_refs)

    with pytest.raises(expected_error):
        signals.prepare_signals(signal, start="2026-07-03", end="2026-07-06",
            access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
            start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)


def test_preparation_reuses_fixed_records_and_model_without_changing_missing_rows(
    tmp_path: Path, prediction_refs: tuple[Path, ...], access: Mock, mocker: MockerFixture,
) -> None:
    read_record = mocker.spy(signals, "read_prediction_record")
    load_model = mocker.spy(signals, "load_prediction_model")
    predict = mocker.patch.object(InferenceModel, "predict", side_effect=AssertionError("fixed predictions"))
    signal = RecordedSignalInput(type="recorded", prediction_refs=tuple(reversed(prediction_refs)))

    signal_inputs_by_ts_us = signals.prepare_signals(signal, start="2026-07-03", end="2026-07-06",
        access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
        start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)

    assert [call.args[0] for call in read_record.call_args_list] == list(reversed(prediction_refs))
    load_model.assert_called_once()
    predict.assert_not_called()
    access.universe.assert_not_called()
    assert tuple(signal_inputs_by_ts_us) == (FIRST_DECISION_TS_US, LAST_DECISION_TS_US)
    for batch, ref in zip(signal_inputs_by_ts_us.values(), prediction_refs, strict=True):
        assert isinstance(batch, signals.RecordedSignalBatch)
        assert batch.prediction_ref == ref
        assert batch.symbols == ("000001", "600000")
        assert batch.event.scores == {"000001": 0.5}


@pytest.mark.parametrize("offset", [0, 1])
def test_each_recorded_model_is_checked_at_its_own_first_decision(
    tmp_path: Path, prediction_refs: tuple[Path, ...], model_ref: Path, access: Mock, offset: int,
) -> None:
    pm = PathManager(tmp_path)
    second_model_ref = tmp_path / "second" / "model.joblib"
    model = load(model_ref)
    save(second_model_ref, model)
    record = TrainingRunRecord.model_validate_json(model_ref.with_name("schedule.json").read_bytes())
    record = record.model_copy(update={
        "model_ref": second_model_ref, "label_maturity_ts_us": LAST_DECISION_TS_US + offset,
    })
    second_model_ref.with_name("schedule.json").write_text(record.model_dump_json())
    features = pd.read_parquet(pm.feature_object(
        feature_set=model.feature_set, version=model.feature_version, trade_date="2026-07-06",
    ).payload_path)
    save_prediction_record(prediction_refs[1], model_ref=second_model_ref, trade_date="2026-07-06",
        decision_ts_us=LAST_DECISION_TS_US, features=features, feature_names=model.feature_names,
        predictions=np.array([0.5, np.nan]))
    signal = RecordedSignalInput(type="recorded", prediction_refs=tuple(reversed(prediction_refs)))
    if offset > 0:
        with pytest.raises(ValueError, match="label maturity"):
            signals.prepare_signals(signal, start="2026-07-03", end="2026-07-06",
                access=access, pm=pm, min_listing_calendar_days=120,
                start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)
        return
    signal_inputs_by_ts_us = signals.prepare_signals(signal, start="2026-07-03", end="2026-07-06",
        access=access, pm=pm, min_listing_calendar_days=120,
        start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)
    assert tuple(signal_inputs_by_ts_us) == (FIRST_DECISION_TS_US, LAST_DECISION_TS_US)


def test_reusing_a_model_does_not_hide_a_different_recorded_digest(
    tmp_path: Path, prediction_refs: tuple[Path, ...], access: Mock,
) -> None:
    with pq.ParquetFile(prediction_refs[0]) as source:
        table = source.read()
    metadata = dict(table.schema.metadata)
    inputs = json.loads(metadata[b"prediction_inputs"])
    inputs["model_sha256"] = "0" * 64
    metadata[b"prediction_inputs"] = json.dumps(inputs).encode()
    pq.write_table(table.replace_schema_metadata(metadata), prediction_refs[0])
    signal = RecordedSignalInput(type="recorded", prediction_refs=tuple(reversed(prediction_refs)))

    with pytest.raises(ValueError, match="model content has changed"):
        signals.prepare_signals(signal, start="2026-07-03", end="2026-07-06",
            access=access, pm=PathManager(tmp_path), min_listing_calendar_days=120,
            start_ts_us=FIRST_DECISION_TS_US, end_ts_us=LAST_DECISION_TS_US + 1)


@pytest.mark.usefixtures("prediction_refs")
def test_model_batch_owns_features_until_prediction_is_produced(
    tmp_path: Path, model_ref: Path,
) -> None:
    pm = PathManager(tmp_path)
    model = load(model_ref)
    features = pd.read_parquet(pm.feature_object(
        feature_set=model.feature_set, version=model.feature_version, trade_date="2026-07-03",
    ).payload_path)
    batch = signals.ModelSignalBatch("2026-07-03", FIRST_DECISION_TS_US, features, model_ref, model)

    features.loc[:, "factor"] = np.nan
    produced = signals.produce_signal(batch, prediction_dir=tmp_path / "produced")

    assert produced.event.scores == {"000001": 0.5}
    assert produced.symbols == ("000001", "600000")
    persisted = pd.read_parquet(produced.prediction_ref)
    np.testing.assert_array_equal(persisted["prediction"].to_numpy(), [0.5, np.nan])


@pytest.mark.usefixtures("prediction_refs")
def test_replay_signal_owns_inputs_and_produces_each_prediction_only_at_its_decision(
    tmp_path: Path, model_ref: Path, access: Mock, mocker: MockerFixture,
) -> None:
    inputs = signals.prepare_signals(ModelSignalInput(type="model", model_ref=model_ref),
        start="2026-07-03", end="2026-07-06", access=access, pm=PathManager(tmp_path),
        min_listing_calendar_days=120, start_ts_us=FIRST_DECISION_TS_US,
        end_ts_us=LAST_DECISION_TS_US + 1)
    prediction_dir = tmp_path / "produced"
    signal = signals.ReplaySignal(inputs, prediction_dir)
    predict = mocker.spy(InferenceModel, "predict")
    inputs.clear()

    assert signal.timestamps() == (FIRST_DECISION_TS_US, LAST_DECISION_TS_US)
    assert signal.at(FIRST_DECISION_TS_US - 1) is None
    assert not prediction_dir.exists()
    predict.assert_not_called()

    first = signal.at(FIRST_DECISION_TS_US)
    assert first is not None
    assert first.event.scores == {"000001": 0.5}
    assert first.prediction_ref.is_file()
    assert signal.at(FIRST_DECISION_TS_US) is first
    assert signal.at(FIRST_DECISION_TS_US + 1) is None
    assert predict.call_count == 1
    assert not (prediction_dir / "2026-07-06").exists()

    last = signal.at(LAST_DECISION_TS_US)
    assert last is not None
    assert last.prediction_ref.is_file()
    assert signal.at(LAST_DECISION_TS_US) is last
    assert signal.at(FIRST_DECISION_TS_US) is first
    assert predict.call_count == 2
