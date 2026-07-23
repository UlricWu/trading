# filepath: src/training/context.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from src.pipeline.artifact import ModelArtifact, PreprocessArtifact
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
    """
    Runtime context for one formal training experiment run.

    The workflow creates this context from accepted job identity. The pipeline
    only mutates the current date fields and passes the context through steps.
    """

    pm: PathManager
    experiment_name: str

    train_start_date: str = ""
    train_end_date: str = ""
    eval_start_date: str = ""
    eval_end_date: str = ""

    trade_date: str = ""
    eval_date: str = ""

    train_X: pd.DataFrame | None = None
    train_y: pd.Series | None = None
    eval_X: pd.DataFrame | None = None
    eval_y: pd.Series | None = None
    eval_pred: np.ndarray | None = None

    preprocess_artifact: PreprocessArtifact | None = None
    model_state: ModelState | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    model_artifact: ModelArtifact | None = None
