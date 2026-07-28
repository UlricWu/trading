# filepath: src/training/artifact.py
from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np

from src.training.inference_model import (
    InferenceModel,
    PredictionModel,
    Preprocessor,
)
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


class _FrozenFloatMapping(Mapping[str, float]):
    """Small immutable, pickle-safe mapping used inside model artifacts."""

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[str, float]) -> None:
        items: list[tuple[str, float]] = []
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError("artifact mapping keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f"artifact mapping value must be numeric: {key}")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"artifact mapping value must be finite: {key}")
            items.append((key, number))
        self._items = tuple(items)

    def __getitem__(self, key: str) -> float:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self.items()) == dict(other.items())

    def __repr__(self) -> str:
        return repr(dict(self._items))


@dataclass(frozen=True, slots=True)
class PreprocessArtifact:
    """Serializable preprocessing parameters learned during training.

    Semantics:
    - Frozen preprocessing parameters learned during training
    - Shared by training / backtest / inference
    - Serializable via joblib

    Contract:
    - feature_columns defines the ONLY valid feature order
    - transform(X) MUST be deterministic and side-effect free

    Example:
        artifact = PreprocessArtifact(
            feature_columns=("momentum",),
            fill_values={"momentum": 0.0},
        )
    """

    # Feature coordinate system
    feature_columns: Sequence[str]

    # Missing-value handling learned from training data
    missing_method: str = "constant"
    fill_values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        columns = tuple(self.feature_columns)
        if not columns or any(
            not isinstance(column, str) or not column for column in columns
        ):
            raise ValueError("feature_columns must contain non-empty strings")
        if len(columns) != len(set(columns)):
            raise ValueError("feature_columns must be unique")
        if self.missing_method not in {"constant", "mean", "median", "drop"}:
            raise ValueError(f"unsupported missing_method: {self.missing_method!r}")
        unknown_fill_columns = set(self.fill_values) - set(columns)
        if unknown_fill_columns:
            raise ValueError(
                "fill_values contain unknown feature columns: "
                f"{sorted(unknown_fill_columns)}"
            )
        if self.missing_method == "drop" and self.fill_values:
            raise ValueError("drop preprocessing must not define fill_values")
        object.__setattr__(self, "feature_columns", columns)
        object.__setattr__(self, "fill_values", _FrozenFloatMapping(self.fill_values))

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Apply missing-value handling learned during training.

        Args:
            X: ndarray of shape (n_samples, n_features)
               Feature order MUST match feature_columns.

        Returns:
            Transformed ndarray with same shape.

        Example:
            transformed = artifact.transform(
                np.array([[float("nan")]], dtype=float)
            )
        """
        if X.ndim != 2 or X.shape[1] != len(self.feature_columns):
            raise ValueError(
                f"[PreprocessArtifact] bad X shape={X.shape}, "
                f"expected (*, {len(self.feature_columns)})"
            )

        Xo = X.astype(float, copy=True)

        # -----------------------------
        # 1) Missing
        # -----------------------------
        if self.missing_method == "drop":
            Xo = Xo[np.isfinite(Xo).all(axis=1)]
        else:
            for j, col in enumerate(self.feature_columns):
                if col not in self.fill_values:
                    continue
                fill_value = float(self.fill_values[col])
                Xo[~np.isfinite(Xo[:, j]), j] = fill_value

        # -----------------------------
        # 2) Final NaN cleanup
        # -----------------------------
        Xo[~np.isfinite(Xo)] = 0.0
        return Xo


# ==================================================
# Model artifact
# ==================================================
@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Inference-ready trained model bundle and its metadata.

    Semantics:
    - Single source of truth for a trained model bundle
    - Owns model + preprocess + metadata
    - Builds inference-ready model (atomic)

    Hard rules:
    - NEVER expose raw model directly
    - Inference MUST go through build_inference_model()

    Example:
        artifact = ModelArtifact(
            model_path=Path("training/model.pkl"),
            preprocess_path=Path("training/preprocess.pkl"),
            model_group="sgd_regression",
            experiment_id="run-1",
            asof_day="2026-07-20",
            created_at=datetime.fromisoformat("2026-07-20T12:00:00"),
            metrics={"ic": 0.1},
            feature_names=("momentum",),
            feature_version="v1",
            feature_set="daily",
            price_adjustment="raw",
            label_lookahead=1,
        )
    """

    model_path: Path
    preprocess_path: Path

    # Identity
    model_group: str
    experiment_id: str
    asof_day: str
    created_at: datetime

    # Metadata
    metrics: Mapping[str, float]
    feature_names: Sequence[str]
    feature_version: str
    feature_set: str
    price_adjustment: str  # raw / qfq / hfq
    label_lookahead: int

    def __post_init__(self) -> None:
        if not isinstance(self.model_path, Path):
            raise TypeError("model_path must be a pathlib.Path")
        if not isinstance(self.preprocess_path, Path):
            raise TypeError("preprocess_path must be a pathlib.Path")
        if self.model_path.parent != self.preprocess_path.parent:
            raise ValueError(
                "model_path and preprocess_path must share a training directory"
            )
        for field_name, value in (
            ("model_group", self.model_group),
            ("experiment_id", self.experiment_id),
            ("feature_set", self.feature_set),
            ("feature_version", self.feature_version),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        DateTimeUtils.require_system_date(self.asof_day, field_name="asof_day")
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime")
        if self.price_adjustment not in {"raw", "qfq", "hfq"}:
            raise ValueError("price_adjustment must be raw, qfq, or hfq")
        if (
            isinstance(self.label_lookahead, bool)
            or not isinstance(self.label_lookahead, int)
            or self.label_lookahead < 0
        ):
            raise ValueError("label_lookahead must be a non-negative int")
        names = tuple(self.feature_names)
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("feature_names must contain non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("feature_names must be unique")
        object.__setattr__(self, "feature_names", names)
        object.__setattr__(self, "metrics", _FrozenFloatMapping(self.metrics))

    # ==================================================
    # Loaders (PRIVATE, INTERNAL)
    # ==================================================
    def _load_model(self) -> PredictionModel:
        if not self.model_path.exists():
            raise RuntimeError(
                f"[ModelArtifact] model.pkl not found: {self.model_path}"
            )
        raw: object = joblib.load(self.model_path)
        if not isinstance(raw, PredictionModel):
            raise RuntimeError("[ModelArtifact] model must expose predict(values)")
        return raw

    def _load_preprocess(self) -> Preprocessor:
        """
        Returns preprocess artifact (opaque to caller).

        Contract:
        - Must expose:
            - feature_columns: list[str]
            - transform(X: np.ndarray) -> np.ndarray
        """
        if not self.preprocess_path.exists():
            raise RuntimeError(
                f"[ModelArtifact] preprocess.pkl not found: {self.preprocess_path}"
            )
        raw: object = joblib.load(self.preprocess_path)
        if not isinstance(raw, Preprocessor):
            raise RuntimeError(
                "[ModelArtifact] preprocess must expose feature_columns and transform"
            )
        return raw

    # ==================================================
    # Inference (🔒 ONLY LEGAL ENTRY)
    # ==================================================
    def build_inference_model(self) -> InferenceModel:
        """Build an inference model as ``preprocess ∘ model``.

        Example:
            inference_model = artifact.build_inference_model()
        """
        model = self._load_model()
        preprocess = self._load_preprocess()

        if list(preprocess.feature_columns) != list(self.feature_names):
            raise RuntimeError(
                "[ModelArtifact] feature order mismatch:\n"
                f"  artifact.feature_names = {self.feature_names}\n"
                f"  preprocess.feature_columns = {preprocess.feature_columns}"
            )

        return InferenceModel(
            model=model,
            preprocess=preprocess,
            feature_names=self.feature_names,
            label_lookahead=self.label_lookahead,
            feature_set=self.feature_set,
            feature_version=self.feature_version,
        )


def resolve_model_artifact(
    *,
    pm: PathManager,
    experiment_name: str,
) -> ModelArtifact:
    """Resolve one inference-ready bundle from a training experiment.

    Example:
        artifact = resolve_model_artifact(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
        )
    """
    meta_path = pm.experiment_training_params(experiment_name=experiment_name)
    if not meta_path.is_file():
        raise RuntimeError(f"[ModelArtifact] params.json not found: {meta_path}")

    meta = _read_json_object(meta_path, label="model metadata")
    metrics_path = pm.experiment_training_metrics(experiment_name=experiment_name)
    metrics_raw: object = _read_json_object(metrics_path, label="model metrics")
    metrics = _parse_metrics(metrics_raw)

    raw_feature_names = _required_field(meta, "feature_names")
    if (
        not isinstance(raw_feature_names, list)
        or not raw_feature_names
        or any(
            not isinstance(name, str) or not name.strip() for name in raw_feature_names
        )
    ):
        raise RuntimeError(
            "[ModelArtifact] feature_names must be a non-empty list of strings"
        )
    feature_names = [str(name) for name in raw_feature_names]
    feature_set = _required_nonempty_string(meta, "feature_set")
    feature_version = _required_nonempty_string(meta, "feature_version")
    created_at_raw = _required_nonempty_string(meta, "created_at")
    try:
        created_at = datetime.fromisoformat(created_at_raw)
    except ValueError as exc:
        raise RuntimeError("[ModelArtifact] created_at must be ISO-8601") from exc
    label_lookahead_raw = _required_field(meta, "label_lookahead")
    if (
        isinstance(label_lookahead_raw, bool)
        or not isinstance(label_lookahead_raw, int)
        or label_lookahead_raw < 0
    ):
        raise RuntimeError("[ModelArtifact] label_lookahead must be non-negative")

    return ModelArtifact(
        model_path=pm.experiment_training_model(experiment_name=experiment_name),
        preprocess_path=pm.experiment_training_preprocess(
            experiment_name=experiment_name
        ),
        model_group=_required_nonempty_string(meta, "model_group"),
        experiment_id=_required_nonempty_string(meta, "experiment_id"),
        asof_day=_required_nonempty_string(meta, "asof_day"),
        created_at=created_at,
        metrics=metrics,
        feature_names=feature_names,
        feature_version=feature_version,
        feature_set=feature_set,
        price_adjustment=_required_nonempty_string(meta, "price_adjustment"),
        label_lookahead=label_lookahead_raw,
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"[ModelArtifact] invalid {label}: {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"[ModelArtifact] {label} must be a JSON object")
    return {str(key): value for key, value in raw.items()}


def _required_field(meta: dict[str, object], key: str) -> object:
    if key not in meta:
        raise RuntimeError(
            f"[ModelArtifact] missing required field in metadata: {key!r}"
        )
    return meta[key]


def _required_nonempty_string(meta: dict[str, object], key: str) -> str:
    value = _required_field(meta, key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"[ModelArtifact] {key} must be a non-empty string")
    return value


def _parse_metrics(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise RuntimeError("[ModelArtifact] metrics must be a JSON object")
    metrics: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RuntimeError(f"[ModelArtifact] metric must be numeric: {key}")
        number = float(value)
        if not math.isfinite(number):
            raise RuntimeError(f"[ModelArtifact] metric must be finite: {key}")
        metrics[str(key)] = number
    return metrics
