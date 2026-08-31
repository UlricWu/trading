# filepath: tests/training/models/test_catalog.py
"""Explicit training-model catalog tests."""

from __future__ import annotations

import pytest

from src.training.models.catalog import get_model_trainer
from src.training.models.sgd_regression import train_sgd_regression


def test_catalog_returns_the_registered_model_trainer() -> None:
    assert get_model_trainer("sgd_regression") is train_sgd_regression


def test_catalog_rejects_an_unregistered_model_group() -> None:
    with pytest.raises(ValueError, match="unsupported model group"):
        get_model_trainer("unknown")
