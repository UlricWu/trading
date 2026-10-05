# filepath: src/training/prediction.py
"""Persist and replay one run's predictions with their exact input evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Self

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, model_validator

from src.access.feature_label import read_features
from src.training.artifact import load
from src.training.inference_model import InferenceModel
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


class PredictionInputs(BaseModel):
    """Identify the fixed model, decision and actual ordered feature input.

    Example:
        inputs, rows = read_prediction_record(Path("/data/run/day.parquet"))
        model_ref = inputs.model_ref
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    model_ref: Path
    model_sha256: str
    feature_sha256: str
    trade_date: str
    decision_ts_us: int

    @model_validator(mode="after")
    def _require_absolute_model(self) -> Self:
        if not self.model_ref.is_absolute():
            raise ValueError("prediction model_ref must be absolute")
        return self


def save_prediction_record(
    ref: Path,
    *,
    model_ref: Path,
    trade_date: str,
    decision_ts_us: int,
    features: pd.DataFrame,
    feature_names: Sequence[str],
    predictions: np.ndarray,
) -> None:
    """Save original keys and nullable predictions to a run-selected path.

    Features are the exact ordered frame used for this prediction, before fitting
    or transforming. Metadata detects changed input without copying its payload.

    Example:
        save_prediction_record(
            Path("/data/run/2026-07-03.parquet"), model_ref=model_ref,
            trade_date="2026-07-03", decision_ts_us=decision_ts_us,
            features=features, feature_names=model.feature_names,
            predictions=model.predict(features[list(model.feature_names)].to_numpy()),
        )
    """
    keys = [column for column in features if column not in feature_names]
    frame = features.loc[:, keys].copy()
    frame["decision_ts_us"] = decision_ts_us
    frame["model_ref"] = str(model_ref)
    frame["prediction"] = predictions
    with model_ref.open("rb") as source:
        model_digest = hashlib.file_digest(source, "sha256").hexdigest()
    inputs = PredictionInputs(
        model_ref=model_ref,
        model_sha256=model_digest,
        feature_sha256=_feature_digest(features),
        trade_date=trade_date,
        decision_ts_us=decision_ts_us,
    )
    table = pa.Table.from_pandas(frame, preserve_index=False)
    metadata = {**(table.schema.metadata or {}), b"prediction_inputs": inputs.model_dump_json().encode()}
    write_parquet_atomic(output_file=ref, table=table.replace_schema_metadata(metadata))


def read_prediction_record(ref: Path) -> tuple[PredictionInputs, pd.DataFrame]:
    """Read the persisted rows and validate their decision and model association.

    Example:
        inputs, rows = read_prediction_record(Path("/data/run/2026-07-03.parquet"))
    """
    with pq.ParquetFile(ref) as source:
        table = source.read()
    inputs = PredictionInputs.model_validate_json(table.schema.metadata[b"prediction_inputs"])
    frame = table.to_pandas()
    keys = [column for column in frame if column not in {"decision_ts_us", "model_ref", "prediction"}]
    table_ops.require_unique(frame, keys, who="prediction record")
    if (
        not frame["model_ref"].eq(str(inputs.model_ref)).all()
        or not frame["decision_ts_us"].eq(inputs.decision_ts_us).all()
        or not frame["trade_date"].eq(inputs.trade_date).all()
    ):
        raise ValueError("prediction rows disagree with their recorded inputs")
    if table.schema.field("prediction").type != pa.float64():
        raise ValueError("prediction records require float64 predictions")
    return inputs, frame


def load_prediction_model(inputs: PredictionInputs) -> InferenceModel:
    """Load the exact model bytes referenced by a persisted prediction record.

    Example:
        model = load_prediction_model(inputs)
    """
    with inputs.model_ref.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != inputs.model_sha256:
        raise ValueError("prediction model content has changed")
    return load(inputs.model_ref)


def verify_prediction_record(ref: Path, *, pm: PathManager) -> None:
    """Replay recorded rows and require exact equality, including missing values.

    Changed source content fails before prediction; evaluation labels are unused.

    Example:
        verify_prediction_record(Path("/data/run/2026-07-03.parquet"), pm=path_manager)
    """
    inputs, recorded = read_prediction_record(ref)
    model = load_prediction_model(inputs)
    features = read_features(
        pm=pm, feature_set=model.feature_set, version=model.feature_version,
        trade_date=inputs.trade_date, feature_names=model.feature_names,
    )
    keys = [column for column in recorded if column not in {"decision_ts_us", "model_ref", "prediction"}]
    selected_keys = pd.MultiIndex.from_frame(recorded.loc[:, keys])
    features = features.set_index(keys).loc[selected_keys].reset_index()
    if _feature_digest(features) != inputs.feature_sha256:
        raise ValueError("prediction feature input has changed")
    predictions = model.predict(features.loc[:, list(model.feature_names)].to_numpy(dtype=np.float64))
    np.testing.assert_array_equal(
        predictions, recorded["prediction"].to_numpy(dtype=np.float64), strict=True,
    )


def _feature_digest(features: pd.DataFrame) -> str:
    table = pa.Table.from_pandas(features, preserve_index=False).replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue()).hexdigest()
