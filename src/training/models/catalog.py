# filepath: src/training/models/catalog.py
"""Resolve configured model groups to explicit training implementations."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

import pandas as pd

from src.training.inference_model import PredictionModel
from src.training.models.sgd_regression import train_sgd_regression


class ModelTrainer(Protocol):
    """Train one fresh prediction model from an aligned tabular dataset.

    Example:
        trainer: ModelTrainer = get_model_trainer("sgd_regression")
        model = trainer(
            X=train_X,
            y=train_y,
            model_params={"random_state": 7},
        )
    """

    def __call__(
        self,
        *,
        X: pd.DataFrame,
        y: pd.Series,
        model_params: Mapping[str, object],
    ) -> PredictionModel:
        """Train and return one fresh prediction model.

        Example:
            model = trainer(
                X=train_X,
                y=train_y,
                model_params={"random_state": 7},
            )
        """
        ...


_MODEL_TRAINERS: Mapping[str, ModelTrainer] = MappingProxyType(
    {
        "sgd_regression": train_sgd_regression,
    }
)


def get_model_trainer(model_group: str) -> ModelTrainer:
    """Return the trainer explicitly registered for one model group.

    Example:
        trainer = get_model_trainer("sgd_regression")
    """
    try:
        return _MODEL_TRAINERS[model_group]
    except KeyError as exc:
        raise ValueError(f"unsupported model group: {model_group!r}") from exc
