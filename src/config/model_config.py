# filepath: src/config/model_config.py
from __future__ import annotations

from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TrainingModelName: TypeAlias = Literal[
    "sgd_regression", "ridge_regression", "logistic_regression",
    "hist_gradient_boosting_regressor", "hist_gradient_boosting_classifier",
]
FeatureGroupName: TypeAlias = Literal[
    "daily_basic_selected_v1", "daily_basic_all_v1", "l2_stock_1430_all_v1",
    "previous_day_daily_v1", "previous_day_daily_l2_price_v1",
    "previous_day_daily_l2_activity_v1", "previous_day_daily_l2_direction_v1",
    "previous_day_daily_l2_v1",
]
LabelGroupName: TypeAlias = Literal[
    "daily_close_return_rank_d1/v1", "daily_close_return_rank_d3/v1",
    "daily_close_return_rank_d5/v1", "l2_stock_1430_t1_vwap_rank/v1",
    "next_day_high_return_rank/v1", "next_day_high_top_decile/v1",
]


def _require_feature_columns(value: list[str]) -> list[str]:
    if any(not column for column in value) or len(value) != len(set(value)):
        raise ValueError("feature_columns must contain unique nonempty names")
    return value


class FeatureGroupConfig(BaseModel):
    """Select an ordered subset of one existing feature dataset.

    Example:
        group = FeatureGroupConfig(
            feature_set="tushare_daily_basic", feature_version="v1",
            feature_columns=["f_d_close_return_1d"],
        )
    """

    model_config = ConfigDict(extra="forbid")

    feature_set: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)
    feature_columns: list[str] = Field(min_length=1)

    _validate_feature_columns = field_validator("feature_columns")(_require_feature_columns)


class FeatureLabelConfig(BaseModel):
    """Resolve feature/label identities and columns for one training run.

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
    feature_columns: list[str] = Field(min_length=1)
    label_set: str = Field(min_length=1)
    label_version: str = Field(min_length=1)
    label_column: str = Field(min_length=1)

    _validate_feature_columns = field_validator("feature_columns")(_require_feature_columns)


class MissingConfig(BaseModel):
    """Missing-value handling owned by a selected model preset.

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
            raise ValueError("preprocessing.missing.fill_value is required")
        if self.method != "constant" and self.fill_value is not None:
            raise ValueError(
                "preprocessing.missing.fill_value is only valid "
                "when method='constant'"
            )
        return self


class PreprocessingConfig(BaseModel):
    """Model-input preprocessing owned by a selected model preset.

    Example:
        config = PreprocessingConfig(
            missing=MissingConfig(method="constant", fill_value=0.0)
        )
    """

    model_config = ConfigDict(extra="forbid")

    missing: MissingConfig = Field(
        default_factory=lambda: MissingConfig(method="constant", fill_value=0.0)
    )


class ModelPreset(BaseModel):
    """Define training behavior independently of the selected data.

    Example:
        preset = ModelPreset(
            group="sgd_regression", model_params={"random_state": 7},
        )
    """

    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1)
    model_params: dict[str, object] = Field(default_factory=dict)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    sample_weight: Literal["uniform", "date_equal"] = "uniform"


class ModelConfig(BaseModel):
    """Combine the selected training preset and actual feature/label configuration.

    Example:
        config = ModelConfig(
            group="sgd_regression",
            dataset=feature_label_config,
        )
    """

    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1)
    model_params: dict[str, object] = Field(default_factory=dict)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    sample_weight: Literal["uniform", "date_equal"] = "uniform"
    dataset: FeatureLabelConfig
