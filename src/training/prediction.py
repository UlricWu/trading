# filepath: src/training/prediction.py
"""Persist and replay predictions identified by their model, decision and sample keys."""

from __future__ import annotations

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
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


class PredictionInputs(BaseModel):
    """Identify the published model and decision associated with prediction rows.

    Example:
        inputs, rows = read_prediction_record(Path("/data/run/day.parquet"))
        model_ref = inputs.model_ref
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    model_ref: Path
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

    Features supply the original ordered sample keys. The referenced published
    model owns the feature and label identities; metadata records the decision.

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
    inputs = PredictionInputs(
        model_ref=model_ref,
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


def verify_prediction_record(ref: Path, *, pm: PathManager) -> None:
    """Replay recorded rows and require exact equality, including missing values.

    The model's declared features are selected by recorded sample keys. Evaluation
    labels are unused, and a failed comparison never rewrites the prediction.

    Example:
        verify_prediction_record(Path("/data/run/2026-07-03.parquet"), pm=path_manager)
    """
    inputs, recorded = read_prediction_record(ref)
    model = load(inputs.model_ref)
    features = read_features(
        pm=pm, feature_set=model.feature_set, version=model.feature_version,
        trade_date=inputs.trade_date, feature_names=model.feature_names,
    )
    keys = [column for column in recorded if column not in {"decision_ts_us", "model_ref", "prediction"}]
    selected_keys = pd.MultiIndex.from_frame(recorded.loc[:, keys])
    features = features.set_index(keys).loc[selected_keys].reset_index()
    predictions = model.predict(features.loc[:, list(model.feature_names)].to_numpy(dtype=np.float64))
    np.testing.assert_array_equal(
        predictions, recorded["prediction"].to_numpy(dtype=np.float64), strict=True,
    )
