# filepath: src/training/context.py
"""Per-window values and explicitly shared evaluation observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.evaluation import ICObservation
from src.training.inference_model import InferenceModel


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    """Identify one complete training selection and its concrete output reference.

    None evaluation fields mean model delivery without evaluation.

    Example:
        window = TrainingWindow(
            train_dates=("2026-07-17", "2026-07-20"),
            model_ref=Path("/data/experiments/run/training/windows/0000/model.joblib"),
            eval_date="2026-07-21", decision_ts_us=1784617200000000,
        )
    """

    train_dates: tuple[str, ...]
    model_ref: Path
    eval_date: str | None = None
    decision_ts_us: int | None = None
    prediction_ref: Path | None = None


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
