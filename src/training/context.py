# filepath: src/training/context.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from src.training.artifact import PreprocessArtifact
from src.utils.path import PathManager


class PredictionModel(Protocol):
    """The model behavior consumed after a training update."""

    def predict(self, values: np.ndarray) -> np.ndarray: ...


@dataclass(slots=True)
class ModelState:
    model: PredictionModel
    asof_day: str
    update_count: int
    warm_start: bool


@dataclass(slots=True)
class TrainingContext:
    """Carry the mutable state of one training experiment.

    Example:
        context = TrainingContext(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
        )
    """

    pm: PathManager
    experiment_name: str

    train_start_date: str = field(init=False)
    train_end_date: str = field(init=False)
    eval_date: str = field(init=False)
    train_X: pd.DataFrame = field(init=False)
    train_y: pd.Series = field(init=False)
    eval_X: pd.DataFrame = field(init=False)
    eval_y: pd.Series = field(init=False)
    preprocess_artifact: PreprocessArtifact = field(init=False)
    model_state: ModelState = field(init=False)
    metrics: dict[str, float] = field(default_factory=dict)
