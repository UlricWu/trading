# filepath: src/training/context.py
"""One training selection, its evaluation days, and ordered Step values."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.evaluation import ICObservation
from src.training.inference_model import InferenceModel


@dataclass(frozen=True, slots=True)
class EvaluationDay:
    """Bind one evaluation date to its prediction time and output location.

    Example:
        day = EvaluationDay(
            trade_date="2026-08-10", decision_ts_us=1786345200000000,
            prediction_ref=Path("/data/run/training/predictions/2026-08-10.parquet"),
        )
    """

    trade_date: str
    decision_ts_us: int
    prediction_ref: Path


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    """Identify one training interval, model, and optional evaluation days.

    Example:
        window = TrainingWindow(
            train_dates=("2026-07-17", "2026-07-20"),
            model_ref=Path("/data/experiments/run/training/model.joblib"),
            evaluations=(evaluation_day,),
        )
    """

    train_dates: tuple[str, ...]
    model_ref: Path
    evaluations: tuple[EvaluationDay, ...] = ()


@dataclass(slots=True)
class TrainingContext:
    """Carry adjacent Step values; evaluation Steps append to shared observations.

    Example:
        context = TrainingContext(window=training_window)
    """

    window: TrainingWindow
    observations: list[ICObservation] = field(default_factory=list)
    train_X: pd.DataFrame | None = None
    train_y: pd.Series | None = None
    preprocess: FittedPreprocessor | None = None
    model: InferenceModel | None = None
