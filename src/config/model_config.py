# filepath: src/config/model_config.py
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AdjustmentRefDataConfig(BaseModel):
    """Formal processed refdata input selected by `model.dataset`.

    Example:
        config = AdjustmentRefDataConfig(
            method="raw",
            dataset_name="adj_factor",
        )
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["raw", "qfq", "hfq"] = "raw"
    dataset_name: str


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
            adjustment=AdjustmentRefDataConfig(
                method="raw",
                dataset_name="adj_factor",
            ),
        )
    """

    model_config = ConfigDict(extra="forbid")

    feature_set: str
    feature_version: str
    label_set: str
    label_version: str
    feature_columns: list[str]
    label_column: str
    drop_na: bool = True
    adjustment: AdjustmentRefDataConfig


class MissingConfig(BaseModel):
    """Missing-value handling selected by `model.preprocessing.missing`.

    Example:
        config = MissingConfig(method="constant", fill_value=0.0)
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["constant", "mean", "median", "drop"] = "constant"
    fill_value: float | None = None

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
    train_window_days: int = 252
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    dataset: FeatureLabelConfig

    @field_validator("train_window_days", mode="before")
    @classmethod
    def _validate_train_window_days(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("model.train_window_days must be an int")
        if value < 0:
            raise ValueError("model.train_window_days must be >= 0")
        return value
