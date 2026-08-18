# filepath: src/training/context.py
"""Per-window Context for ordered offline training Steps."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    """Identify complete training dates and one evaluation date.

    Example:
        window = TrainingWindow(
            train_dates=("2026-07-17", "2026-07-20"),
            eval_date="2026-07-21",
        )
    """

    train_dates: tuple[str, ...]
    eval_date: str


@dataclass(slots=True)
class TrainingContext:
    """Carry only values shared by Steps for one training window.

    Example:
        context = TrainingContext(
            window=TrainingWindow(
                train_dates=("2026-07-17", "2026-07-20"),
                eval_date="2026-07-21",
            )
        )
    """

    window: TrainingWindow
    metrics: dict[str, float] = field(default_factory=dict)
    train_X: pd.DataFrame | None = None
    train_y: pd.Series | None = None
    eval_X: pd.DataFrame | None = None
    eval_y: pd.Series | None = None
    preprocess: FittedPreprocessor | None = None
    model: InferenceModel | None = None
