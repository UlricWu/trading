# filepath: src/trading/backtest/market/signals.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd

from src.access import Access
from src.access.feature_label import read_features
from src.jobs.requests import RecordedSignalInput, SignalInput
from src.trading.core.events import SignalEvent
from src.training.artifact import load
from src.training.inference_model import InferenceModel
from src.training.prediction import load_prediction_model, read_prediction_record, save_prediction_record
from src.training.record import TrainingRunRecord
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class RecordedSignalBatch:
    """Fixed scores with original missing-row coverage and prediction identity.

    Example:
        batch = RecordedSignalBatch("2026-07-03", event, ("600000",), prediction_ref)
    """

    trade_date: str
    event: SignalEvent
    symbols: tuple[str, ...]
    prediction_ref: Path


@dataclass(frozen=True, slots=True)
class ModelSignalBatch:
    """Only the features belonging to this decision, ready for direct inference.

    Example:
        batch = ModelSignalBatch("2026-07-03", decision_us, features, model_ref, model)
    """

    trade_date: str
    ts_us: int
    features: pd.DataFrame
    model_ref: Path
    model: InferenceModel


SignalBatch = RecordedSignalBatch | ModelSignalBatch


def validate_model_use(*, model_ref: Path, model: InferenceModel, first_decision_ts_us: int) -> None:
    """Check the delivered training record and its theoretical maturity lower bound.

    This is a conditional backtest check, not proof the model existed historically.

    Example:
        validate_model_use(model_ref=ref, model=model, first_decision_ts_us=decision_us)
    """
    record = TrainingRunRecord.model_validate_json(model_ref.with_name("schedule.json").read_bytes())
    if record.model_ref != model_ref or record.train_dates != model.train_dates:
        raise ValueError("model disagrees with its referenced training record")
    dataset = record.configuration.dataset
    if (model.feature_set, model.feature_version, model.feature_names, model.label_set,
        model.label_version, model.label_column) != (
        dataset.feature_set, dataset.feature_version, tuple(dataset.feature_columns),
        dataset.label_set, dataset.label_version, dataset.label_column,
    ):
        raise ValueError("model dataset disagrees with its referenced training record")
    if record.label_maturity_ts_us > first_decision_ts_us:
        raise ValueError("model use requires training label maturity <= first backtest decision")


def prepare_signals(
    signal: SignalInput, *, start: str, end: str, access: Access,
    pm: PathManager, min_listing_calendar_days: int,
) -> dict[int, SignalBatch]:
    """Prepare fixed records or visible feature batches without reading evaluation labels.

    Example:
        batches = prepare_signals(signal, start="2026-07-03", end="2026-07-03",
                                  access=access, pm=pm, min_listing_calendar_days=120)
    """
    batches: dict[int, SignalBatch] = {}
    if isinstance(signal, RecordedSignalInput):
        for ref in signal.prediction_refs:
            inputs, frame = read_prediction_record(ref)
            if not start <= inputs.trade_date <= end:
                continue
            load_prediction_model(inputs)
            if frame["symbol"].duplicated().any():
                raise ValueError("one signal batch requires unique symbols")
            values = frame["prediction"].to_numpy(dtype=float)
            if np.isinf(values).any():
                raise ValueError("predictions must be finite or missing")
            event = SignalEvent(inputs.decision_ts_us, {
                str(symbol): float(value) for symbol, value in zip(frame["symbol"], values, strict=True)
                if not np.isnan(value)
            })
            if event.ts_us in batches:
                raise ValueError("signal decisions must have unique timestamps")
            batches[event.ts_us] = RecordedSignalBatch(inputs.trade_date, event,
                                                       tuple(frame["symbol"]), ref)
        return dict(sorted(batches.items()))

    model = load(signal.model_ref)
    for day in access.trade_dates(start_date=start, end_date=end):
        features = read_features(pm=pm, feature_set=model.feature_set, version=model.feature_version,
                                 trade_date=day, feature_names=model.feature_names)
        eligible = access.universe(trade_date=day, min_listing_calendar_days=min_listing_calendar_days)
        features = features.loc[features["symbol"].isin(eligible)].copy()
        if "decision_ts_utc" in features:
            groups = tuple(features.groupby("decision_ts_utc", sort=True))
        else:
            ts_us = DateTimeUtils.local_time_to_utc_epoch_us(time(15), date.fromisoformat(day))
            groups = ((ts_us, features),)
        for ts_us, frame in groups:
            timestamp = int(ts_us)
            if frame["symbol"].duplicated().any() or timestamp in batches:
                raise ValueError("signal decisions require unique timestamps and symbols")
            batches[timestamp] = ModelSignalBatch(day, timestamp, frame.copy(), signal.model_ref, model)
    if batches:
        validate_model_use(model_ref=signal.model_ref, model=model, first_decision_ts_us=min(batches))
    return dict(sorted(batches.items()))


def produce_signal(batch: SignalBatch, *, prediction_dir: Path) -> RecordedSignalBatch:
    """Run inference at its decision and persist exactly those original rows once.

    Example:
        produced = produce_signal(batch, prediction_dir=run_dir / "predictions")
    """
    if isinstance(batch, RecordedSignalBatch):
        return batch
    symbols = tuple(str(symbol) for symbol in batch.features["symbol"])
    predictions = batch.model.predict(batch.features.loc[:, list(batch.model.feature_names)].to_numpy(dtype=np.float64))
    ref = prediction_dir / batch.trade_date / f"{batch.ts_us}.parquet"
    save_prediction_record(ref, model_ref=batch.model_ref, trade_date=batch.trade_date,
        decision_ts_us=batch.ts_us, features=batch.features, feature_names=batch.model.feature_names,
        predictions=predictions)
    event = SignalEvent(batch.ts_us, {symbol: float(value)
        for symbol, value in zip(symbols, predictions, strict=True) if not np.isnan(value)})
    return RecordedSignalBatch(batch.trade_date, event, symbols, ref)


def validate_recorded_model_uses(batches: Mapping[int, SignalBatch]) -> None:
    """Apply the same training boundary to each fixed model's first trading decision.

    Example:
        validate_recorded_model_uses(batches)
    """
    first_uses: set[Path] = set()
    for ts_us, batch in sorted(batches.items()):
        if isinstance(batch, RecordedSignalBatch):
            inputs, _ = read_prediction_record(batch.prediction_ref)
            if inputs.model_ref not in first_uses:
                model = load_prediction_model(inputs)
                validate_model_use(model_ref=inputs.model_ref, model=model, first_decision_ts_us=ts_us)
                first_uses.add(inputs.model_ref)
