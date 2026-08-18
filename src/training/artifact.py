# filepath: src/training/artifact.py
"""Publish and load the exact persisted training artifact schema."""

from __future__ import annotations

import json
import math
import pickle
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import cast

import joblib

from src.training.inference_model import InferenceModel
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class TrainingParams:
    """Represent the complete persisted training-parameter schema.

    Example:
        params = TrainingParams(
            experiment_id="run-1",
            model_group="sgd_regression",
            asof_day="2026-07-20",
            feature_set="daily",
            feature_version="v1",
            feature_names=("momentum",),
            label_set="rank",
            label_version="v1",
            label_column="target",
            label_lookahead=1,
        )
    """

    experiment_id: str
    model_group: str
    asof_day: str
    feature_set: str
    feature_version: str
    feature_names: tuple[str, ...]
    label_set: str
    label_version: str
    label_column: str
    label_lookahead: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("experiment_id", self.experiment_id),
            ("model_group", self.model_group),
            ("feature_set", self.feature_set),
            ("feature_version", self.feature_version),
            ("label_set", self.label_set),
            ("label_version", self.label_version),
            ("label_column", self.label_column),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        DateTimeUtils.require_system_date(self.asof_day, field_name="asof_day")

        feature_names = tuple(self.feature_names)
        if not feature_names or any(
            not isinstance(name, str) or not name for name in feature_names
        ):
            raise ValueError("feature_names must contain non-empty strings")
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("feature_names must be unique")
        if isinstance(self.label_lookahead, bool) or not isinstance(
            self.label_lookahead, int
        ):
            raise TypeError("label_lookahead must be an int")
        if self.label_lookahead < 0:
            raise ValueError("label_lookahead must be a non-negative int")
        object.__setattr__(self, "feature_names", feature_names)


def persist_training_artifacts(
    *,
    pm: PathManager,
    experiment_name: str,
    params: TrainingParams,
    metrics: Mapping[str, float],
    inference_model: InferenceModel,
) -> None:
    """Atomically publish params, metrics, then the ready inference asset.

    Example:
        persist_training_artifacts(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
            params=params,
            metrics={"ic@2026-07-20": 0.1},
            inference_model=inference_model,
        )
    """
    if not isinstance(inference_model, InferenceModel):
        raise TypeError("inference_model must be an InferenceModel")
    if inference_model.feature_names != params.feature_names:
        raise ValueError("params feature_names must match fitted feature_names")
    if inference_model.feature_set != params.feature_set:
        raise ValueError("params feature_set must match inference_model")
    if inference_model.feature_version != params.feature_version:
        raise ValueError("params feature_version must match inference_model")

    validated_metrics = _validated_training_metrics(metrics)
    FileSystem.ensure_dir(pm.experiment_training_dir(experiment_name=experiment_name))
    FileSystem.write_bytes_atomic(
        pm.experiment_training_params(experiment_name=experiment_name),
        _json_bytes(asdict(params)),
    )
    FileSystem.write_bytes_atomic(
        pm.experiment_training_metrics(experiment_name=experiment_name),
        _json_bytes(validated_metrics),
    )
    inference_path = pm.experiment_training_inference(experiment_name=experiment_name)
    with FileSystem.atomic_path(inference_path) as temporary_path:
        joblib.dump(inference_model, temporary_path)


def load_inference_model(
    *,
    pm: PathManager,
    experiment_name: str,
) -> InferenceModel:
    """Load the single ready inference asset for one training experiment.

    Example:
        inference_model = load_inference_model(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
        )
        feature_names = inference_model.feature_names
    """
    inference_path = pm.experiment_training_inference(experiment_name=experiment_name)
    if not inference_path.is_file():
        raise FileNotFoundError(f"training inference asset not found: {inference_path}")
    try:
        loaded: object = joblib.load(inference_path)
    except (
        AttributeError,
        EOFError,
        ImportError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        pickle.UnpicklingError,
    ) as exc:
        raise ValueError(f"invalid training inference asset: {inference_path}") from exc
    if not isinstance(loaded, InferenceModel):
        raise TypeError("training inference asset must contain an InferenceModel")
    return loaded


def load_training_report_inputs(
    *,
    pm: PathManager,
    experiment_name: str,
) -> tuple[TrainingParams, dict[str, float]]:
    """Load and validate the persisted inputs needed by the report.

    Example:
        params, metrics = load_training_report_inputs(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
        )
    """
    params_payload = _read_json_object(
        pm.experiment_training_params(experiment_name=experiment_name),
        label="training params",
    )
    metrics_payload = _read_json_object(
        pm.experiment_training_metrics(experiment_name=experiment_name),
        label="training metrics",
    )
    return (
        _parse_training_params(params_payload),
        _validated_training_metrics(metrics_payload),
    )


def _parse_training_params(payload: Mapping[str, object]) -> TrainingParams:
    payload_fields = set(payload)
    expected_fields = {field.name for field in fields(TrainingParams)}
    if payload_fields != expected_fields:
        missing = sorted(expected_fields - payload_fields)
        extra = sorted(payload_fields - expected_fields)
        raise ValueError(
            f"training params fields must match schema: missing={missing} extra={extra}"
        )

    feature_names = payload["feature_names"]
    if not isinstance(feature_names, list):
        raise TypeError("training params feature_names must be a list")
    return TrainingParams(
        experiment_id=cast(str, payload["experiment_id"]),
        model_group=cast(str, payload["model_group"]),
        asof_day=cast(str, payload["asof_day"]),
        feature_set=cast(str, payload["feature_set"]),
        feature_version=cast(str, payload["feature_version"]),
        feature_names=tuple(feature_names),
        label_set=cast(str, payload["label_set"]),
        label_version=cast(str, payload["label_version"]),
        label_column=cast(str, payload["label_column"]),
        label_lookahead=cast(int, payload["label_lookahead"]),
    )


def _validated_training_metrics(
    metrics: Mapping[str, object],
) -> dict[str, float]:
    if not metrics:
        raise ValueError("training metrics must not be empty")
    validated: list[tuple[str, float]] = []
    for key, raw_value in metrics.items():
        if not isinstance(key, str):
            raise TypeError("training metric keys must be strings")
        metric_date = key.removeprefix("ic@")
        if metric_date == key:
            raise ValueError(f"invalid training metric key: {key!r}")
        DateTimeUtils.require_system_date(
            metric_date,
            field_name=f"training metric {key!r} date",
        )
        if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
            raise TypeError(f"training metric {key} must be a finite number")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"training metric {key} must be a finite number")
        validated.append((key, value))
    validated.sort(key=lambda item: item[0])
    return dict(validated)


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object")
    return payload


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
