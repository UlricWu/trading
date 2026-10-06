# filepath: src/training/evaluation.py
"""Record fixed-prediction Rank IC observations and their explicit coverage."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from src.access.feature_label import read_labels
from src.training.engines.rank_ic import rank_ic
from src.training.inference_model import InferenceModel
from src.training.prediction import PredictionInputs, load_prediction_model, read_prediction_record
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class ICObservation(BaseModel):
    """One model's evaluation at one UTC epoch microsecond decision time.

    Example:
        observation = ICObservation(
            model_ref="/data/models/001.joblib", decision_ts_us=1,
            input_count=2, no_prediction_count=0, missing_label_count=0,
            value=1.0, pair_count=2, undefined_reasons=(),
        )
        assert observation.pair_count == 2
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    model_ref: str
    decision_ts_us: int
    input_count: int = Field(ge=0)
    no_prediction_count: int = Field(ge=0)
    missing_label_count: int = Field(ge=0)
    value: FiniteFloat | None
    pair_count: int = Field(ge=0)
    undefined_reasons: tuple[
        Literal["insufficient_pairs", "constant_prediction", "constant_label"], ...
    ]
    prediction_ref: str | None = None
    label_ref: str | None = None
    label_sha256: str | None = None


class ICSummary(BaseModel):
    """Retain every observation and average only defined dates equally.

    Example:
        summary = ICSummary(date_count=1, defined_date_count=1,
                            mean_ic=1.0, undefined_reason_counts={})
        assert summary.date_count == 1
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    date_count: int = Field(ge=0)
    defined_date_count: int = Field(ge=0)
    mean_ic: FiniteFloat | None
    undefined_reason_counts: dict[str, int]


class ICEvaluation(BaseModel):
    """Represent the persisted evaluation JSON schema.

    Example:
        evaluation = ICEvaluation(
            observations=(observation,),
            summary=summarize_observations((observation,)),
        )
        payload = evaluation.model_dump(mode="json")
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    observations: tuple[ICObservation, ...]
    summary: ICSummary


@dataclass(frozen=True, slots=True)
class EvaluationLabels:
    """Keep aligned label values with the exact source revision used to read them.

    Example:
        labels = read_evaluation_labels(pm=pm, inputs=inputs, model=model, predictions=rows)
        assert labels.values.shape == (len(rows),)
    """

    values: np.ndarray
    ref: Path
    sha256: str


def evaluate_predictions(
    *,
    model_ref: Path,
    decision_ts_us: int,
    predictions: np.ndarray,
    labels: np.ndarray,
) -> ICObservation:
    """Evaluate aligned arrays without loading a model or changing predictions.

    Missing-label counts exclude rows that have no prediction.

    Example:
        observation = evaluate_predictions(
            model_ref=Path("/data/models/001.joblib"), decision_ts_us=1,
            predictions=np.array([0.0, np.nan]), labels=np.array([1.0, np.nan]),
        )
    """
    value, pair_count, reasons = rank_ic(predictions, labels)
    predicted = np.isfinite(predictions)
    return ICObservation(
        model_ref=str(model_ref), decision_ts_us=decision_ts_us,
        input_count=len(predictions), no_prediction_count=int((~predicted).sum()),
        missing_label_count=int((predicted & np.isnan(labels)).sum()),
        value=value, pair_count=pair_count, undefined_reasons=reasons,
    )


def evaluate_prediction_record(ref: Path, *, pm: PathManager) -> ICObservation:
    """Evaluate saved predictions against the model's target without predicting.

    Labels are selected by the complete recorded keys and never change the input
    population. Their content digest identifies this evaluation's label revision.

    Example:
        observation = evaluate_prediction_record(
            Path("/data/run/2026-07-03.parquet"), pm=path_manager,
        )
    """
    inputs, predictions = read_prediction_record(ref)
    model = load_prediction_model(inputs)
    labels = read_evaluation_labels(pm=pm, inputs=inputs, model=model, predictions=predictions)
    observation = evaluate_predictions(
        model_ref=inputs.model_ref, decision_ts_us=inputs.decision_ts_us,
        predictions=predictions["prediction"].to_numpy(dtype=np.float64), labels=labels.values,
    )
    return observation.model_copy(update={
        "prediction_ref": str(ref), "label_ref": str(labels.ref), "label_sha256": labels.sha256,
    })


def read_evaluation_labels(
    *, pm: PathManager, inputs: PredictionInputs, model: InferenceModel,
    predictions: pd.DataFrame,
) -> EvaluationLabels:
    """Select labels by complete prediction keys and retain their source digest.

    Example:
        labels = read_evaluation_labels(pm=pm, inputs=inputs, model=model, predictions=rows)
        errors = rows["prediction"].to_numpy() - labels.values
    """
    labels = read_labels(
        pm=pm, label_set=model.label_set, version=model.label_version,
        trade_date=inputs.trade_date, label_column=model.label_column,
    )
    keys = list(labels.columns[:-1])
    prediction_keys = predictions.drop(columns=["decision_ts_us", "model_ref", "prediction"])
    if tuple(prediction_keys.columns) != tuple(keys):
        raise RuntimeError("evaluation prediction / label key mismatch")
    positions = pd.MultiIndex.from_frame(labels[keys]).get_indexer(
        pd.MultiIndex.from_frame(prediction_keys),
    )
    if (positions < 0).any():
        raise RuntimeError("evaluation prediction / label key mismatch")
    label_ref = pm.label_object(
        label_set=model.label_set, version=model.label_version, trade_date=inputs.trade_date,
    ).payload_path
    with label_ref.open("rb") as source:
        label_digest = hashlib.file_digest(source, "sha256").hexdigest()
    return EvaluationLabels(
        values=labels[model.label_column].iloc[positions].to_numpy(dtype=np.float64),
        ref=label_ref, sha256=label_digest,
    )


def summarize_observations(observations: Sequence[ICObservation]) -> ICSummary:
    """Summarize equal-weight decision dates with explicit undefined coverage.

    Example:
        summary = summarize_observations((observation,))
        coverage = (summary.defined_date_count, summary.date_count)
    """
    defined = [item.value for item in observations if item.value is not None]
    counts = Counter(reason for item in observations for reason in item.undefined_reasons)
    return ICSummary(
        date_count=len(observations), defined_date_count=len(defined),
        mean_ic=float(np.mean(defined)) if defined else None,
        undefined_reason_counts=dict(sorted(counts.items())),
    )


def save_evaluation(ref: Path, observations: Sequence[ICObservation]) -> None:
    """Publish evaluation JSON with null values and no non-standard NaN tokens.

    Example:
        save_evaluation(Path("/data/experiments/run/training/metrics.json"), (observation,))
    """
    evaluation = ICEvaluation(
        observations=tuple(observations), summary=summarize_observations(observations),
    )
    payload = json.dumps(
        evaluation.model_dump(mode="json"), ensure_ascii=False, indent=2, allow_nan=False,
    ).encode("utf-8")
    FileSystem.write_bytes_atomic(ref, payload)


def load_evaluation(ref: Path) -> ICEvaluation:
    """Read the evaluation schema, preserving null and reason information.

    Example:
        evaluation = load_evaluation(Path("/data/experiments/run/training/metrics.json"))
        mean_ic = evaluation.summary.mean_ic
    """
    evaluation = ICEvaluation.model_validate_json(ref.read_bytes())
    for item in evaluation.observations:
        if item.input_count != item.no_prediction_count + item.missing_label_count + item.pair_count:
            raise ValueError("evaluation row counts must partition input_count")
        if (item.value is None) != bool(item.undefined_reasons):
            raise ValueError("undefined evaluation values must carry reasons")
    if evaluation.summary != summarize_observations(evaluation.observations):
        raise ValueError("evaluation summary must match its observations")
    return evaluation
