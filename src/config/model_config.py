# filepath: src/config/model_config.py
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FeatureLabelConfig(BaseModel):
    """Formal feature/label input selection parsed from `model.dataset`.

    Example:
        config = FeatureLabelConfig(
            feature_set="daily",
            feature_version="v1",
            label_set="rank",
            label_version="v1",
            feature_columns=["momentum"],
            label_column="target",
        )
    """

    model_config = ConfigDict(extra="forbid")

    feature_set: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)
    label_set: str = Field(min_length=1)
    label_version: str = Field(min_length=1)
    feature_columns: list[str] = Field(min_length=1)
    label_column: str = Field(min_length=1)

    @field_validator("feature_columns")
    @classmethod
    def _validate_feature_columns(cls, value: list[str]) -> list[str]:
        if any(not column for column in value):
            raise ValueError(
                "model.dataset.feature_columns must not contain empty names"
            )
        if len(value) != len(set(value)):
            raise ValueError("model.dataset.feature_columns must be unique")
        return value


class MissingConfig(BaseModel):
    """Missing-value handling selected by `model.preprocessing.missing`.

    Example:
        config = MissingConfig(method="constant", fill_value=0.0)
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["constant", "mean", "median", "drop"] = "constant"
    fill_value: float | None = Field(
        default=None,
        strict=True,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def _validate_fill_value(self) -> Self:
        if self.method == "constant" and self.fill_value is None:
            raise ValueError("model.preprocessing.missing.fill_value is required")
        if self.method != "constant" and self.fill_value is not None:
            raise ValueError(
                "model.preprocessing.missing.fill_value is only valid "
                "when method='constant'"
            )
        return self


class PreprocessingConfig(BaseModel):
    """Model-input preprocessing parsed from `model.preprocessing`.

    Example:
        config = PreprocessingConfig(
            missing=MissingConfig(method="constant", fill_value=0.0)
        )
    """

    model_config = ConfigDict(extra="forbid")

    missing: MissingConfig = Field(
        default_factory=lambda: MissingConfig(method="constant", fill_value=0.0)
    )


class ModelConfig(BaseModel):
    """Training model definition parsed from the `model` config section.

    Example:
        config = ModelConfig(
            group="sgd_regression",
            dataset=feature_label_config,
            train_window_days=30,
        )
    """

    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1)
    model_params: dict[str, object] = Field(default_factory=dict)
    train_window_days: int = Field(default=252, ge=0, strict=True)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    dataset: FeatureLabelConfig
