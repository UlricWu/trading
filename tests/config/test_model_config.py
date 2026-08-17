# filepath: tests/config/test_model_config.py
"""Schema tests for static training configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.model_config import MissingConfig, ModelConfig


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
        },
    }


def test_model_config_uses_explicit_ordered_feature_columns() -> None:
    config = ModelConfig.model_validate(_model_payload())

    assert config.group == "sgd_regression"
    assert config.dataset.feature_columns == ["factor"]
    assert not hasattr(config.preprocessing, "version")
    assert not hasattr(config.dataset, "adjustment")
    assert not hasattr(config.dataset, "drop_na")


@pytest.mark.parametrize("removed_field", ["adjustment", "drop_na"])
def test_model_config_rejects_removed_dataset_fields(removed_field: str) -> None:
    payload = _model_payload()
    dataset = payload["dataset"]
    assert isinstance(dataset, dict)
    dataset[removed_field] = (
        {"method": "raw", "dataset_name": "adj_factor"}
        if removed_field == "adjustment"
        else True
    )

    with pytest.raises(ValidationError):
        ModelConfig.model_validate(payload)


@pytest.mark.parametrize("feature_columns", [[], ["factor", "factor"], [""]])
def test_model_config_rejects_ambiguous_feature_identity(
    feature_columns: list[str],
) -> None:
    payload = _model_payload()
    dataset = payload["dataset"]
    assert isinstance(dataset, dict)
    dataset["feature_columns"] = feature_columns

    with pytest.raises(ValidationError):
        ModelConfig.model_validate(payload)


@pytest.mark.parametrize("fill_value", [float("nan"), float("inf"), True])
def test_constant_missing_fill_requires_an_explicit_finite_number(
    fill_value: object,
) -> None:
    with pytest.raises(ValidationError):
        MissingConfig(method="constant", fill_value=fill_value)  # type: ignore[arg-type]


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
