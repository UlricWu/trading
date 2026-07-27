# filepath: src/config/backtest_config.py
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BacktestMode(str, Enum):
    """Fixed daily_alpha five-layer backtest modes accepted by config."""

    SIGNAL_EVAL = "signal_eval"
    TRADABLE_ALPHA_EVAL = "tradable_alpha_eval"
    EXECUTION_EVAL = "execution_eval"
    RISK_EVAL = "risk_eval"
    FULL_BACKTEST = "full_backtest"


class BacktestModelRef(BaseModel):
    """
    Reference to a training experiment artifact consumed by mode builders.

    `name` is the training experiment namespace under `experiments/{name}/`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str


class ThresholdStrategyParams(BaseModel):
    """Validated parameters for the threshold portfolio constructor."""

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(strict=True, allow_inf_nan=False)
    target_quantity: int = Field(default=100, gt=0, strict=True)


class ThresholdStrategyConfig(BaseModel):
    """Select the threshold portfolio constructor and its parameters."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["threshold"]
    params: ThresholdStrategyParams


class TopKHysteresisStrategyParams(BaseModel):
    """Validated parameters for the top-k hysteresis constructor."""

    model_config = ConfigDict(extra="forbid")

    max_positions: int = Field(gt=0, strict=True)
    entry_threshold: float = Field(strict=True, allow_inf_nan=False)
    exit_threshold: float = Field(strict=True, allow_inf_nan=False)
    rebalance_interval_minutes: int = Field(default=1, gt=0, strict=True)
    keep_winners: bool = Field(default=False, strict=True)
    target_quantity: int = Field(default=100, gt=0, strict=True)

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> Self:
        if self.exit_threshold > self.entry_threshold:
            raise ValueError("exit_threshold must be <= entry_threshold")
        return self


class TopKHysteresisStrategyConfig(BaseModel):
    """Select the top-k hysteresis constructor and its parameters."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["topk_hysteresis"]
    params: TopKHysteresisStrategyParams


StrategyConfig: TypeAlias = Annotated[
    ThresholdStrategyConfig | TopKHysteresisStrategyConfig,
    Field(discriminator="type"),
]


class BacktestConfig(BaseModel):
    """
    Minimal daily_alpha backtest config parsed by AppConfig.

    `backtest_mode` selects a named backtest component profile. Workflow steps
    do not read this value; it is interpreted only by the component builder.
    The minimum listing age is explicit and is forwarded unchanged to Access.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    dates: list[str]

    strategy: StrategyConfig

    model: BacktestModelRef | None = None
    init_cash: int
    backtest_mode: BacktestMode
    min_listing_calendar_days: int = Field(ge=0, strict=True)
