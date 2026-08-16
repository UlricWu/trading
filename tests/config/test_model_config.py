# filepath: tests/config/test_model_config.py
"""Schema tests for static training configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.model_config import ModelConfig


def _model_payload() -> dict[str, object]:
    return {
        "group": "sgd_regression",
        "model_params": {},
        "train_window_days": 30,
        "preprocessing": {
            "missing": {"method": "constant", "fill_value": 0.0},
        },
        "dataset": {
            "feature_set": "features",
            "feature_version": "v1",
            "label_set": "labels",
            "label_version": "v1",
            "feature_columns": ["factor"],
            "label_column": "target",
            "drop_na": True,
            "adjustment": {
                "method": "raw",
                "dataset_name": "adj_factor",
            },
        },
    }


def test_model_config_uses_no_preprocessing_or_adjustment_version_selector() -> None:
    config = ModelConfig.model_validate(_model_payload())

    assert config.group == "sgd_regression"
    assert not hasattr(config.preprocessing, "version")
    assert not hasattr(config.dataset.adjustment, "version")


@pytest.mark.parametrize("field_owner", ["preprocessing", "adjustment"])
def test_model_config_rejects_removed_version_fields(field_owner: str) -> None:
    payload = _model_payload()
    if field_owner == "preprocessing":
        preprocessing = payload["preprocessing"]
        assert isinstance(preprocessing, dict)
        preprocessing["version"] = "v1"
    else:
        dataset = payload["dataset"]
        assert isinstance(dataset, dict)
        adjustment = dataset["adjustment"]
        assert isinstance(adjustment, dict)
        adjustment["version"] = "v1"

    with pytest.raises(ValidationError):
        ModelConfig.model_validate(payload)


def test_model_config_requires_explicit_nonempty_group() -> None:
    payload = _model_payload()
    payload["group"] = ""

    with pytest.raises(ValidationError):
        ModelConfig.model_validate(payload)


def test_model_config_rejects_missing_group() -> None:
    payload = _model_payload()
    del payload["group"]

    with pytest.raises(ValidationError):
        ModelConfig.model_validate(payload)
