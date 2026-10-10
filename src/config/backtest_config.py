# filepath: src/config/backtest_config.py
from __future__ import annotations

from enum import Enum
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


from src.trading.execution.policies.constraints import QuantityRules, QuoteRule
from src.utils.datetime_utils import DateTimeUtils

NonnegativeQuantity = Annotated[int, Field(strict=True, ge=0)]
NonnegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class BacktestMode(str, Enum):
    """Fixed backtest task modes accepted by a submission.

    Example:
        mode = BacktestMode.FULL_BACKTEST
    """

    SIGNAL_EVAL = "signal_eval"
    TRADABLE_ALPHA_EVAL = "tradable_alpha_eval"
    EXECUTION_EVAL = "execution_eval"
    RISK_EVAL = "risk_eval"
    FULL_BACKTEST = "full_backtest"


class ThresholdStrategyParams(BaseModel):
    """Validated parameters for the threshold portfolio constructor.

    Example:
        params = ThresholdStrategyParams(threshold=0.5)
    """

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(strict=True, allow_inf_nan=False)
    target_quantity: int = Field(default=100, gt=0, strict=True)


class ThresholdStrategyConfig(BaseModel):
    """Select the threshold portfolio constructor and its parameters.

    Example:
        strategy = ThresholdStrategyConfig(
            type="threshold",
            params=ThresholdStrategyParams(threshold=0.5),
        )
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["threshold"]
    params: ThresholdStrategyParams
    order_window_minutes: int | None = Field(default=None, gt=0, strict=True)
    quote: QuoteRule | None = None


class TopKHysteresisStrategyParams(BaseModel):
    """Validated parameters for the top-k hysteresis constructor.

    Example:
        params = TopKHysteresisStrategyParams(
            max_positions=10,
            entry_threshold=0.5,
            exit_threshold=0.2,
        )
    """

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
    """Select the top-k hysteresis constructor and its parameters.

    Example:
        strategy = TopKHysteresisStrategyConfig(
            type="topk_hysteresis",
            params=TopKHysteresisStrategyParams(
                max_positions=10,
                entry_threshold=0.5,
                exit_threshold=0.2,
            ),
        )
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["topk_hysteresis"]
    params: TopKHysteresisStrategyParams
    order_window_minutes: int | None = Field(default=None, gt=0, strict=True)
    quote: QuoteRule | None = None


class FixedHoldingStrategyParams(BaseModel):
    """Choose an equal-cash slot count and a next-session exit at explicit local times.

    Example:
        params = FixedHoldingStrategyParams(max_positions=2, decision_time="14:30:00",
                                            exit_time="14:00:00", initial_exit_dates={})
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_positions: int = Field(gt=0, strict=True)
    decision_time: time
    exit_time: time
    initial_exit_dates: dict[str, str]

    @model_validator(mode="after")
    def _validate_times(self) -> Self:
        if self.decision_time.tzinfo is not None or self.exit_time.tzinfo is not None:
            raise ValueError("strategy times use the declared market timezone, without offsets")
        for day in self.initial_exit_dates.values():
            DateTimeUtils.require_system_date(day, field_name="initial_exit_dates")
        return self


class FixedHoldingStrategyConfig(BaseModel):
    """Select the fixed-list, equal-cash-slot, next-trading-day baseline.

    Example:
        strategy = FixedHoldingStrategyConfig(type="fixed_holding", params=params,
                                               order_window_minutes=2)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["fixed_holding"]
    params: FixedHoldingStrategyParams
    order_window_minutes: int = Field(gt=0, strict=True)
    quote: QuoteRule | None = None


StrategyConfig: TypeAlias = Annotated[
    ThresholdStrategyConfig | TopKHysteresisStrategyConfig | FixedHoldingStrategyConfig,
    Field(discriminator="type"),
]


class BacktestConfig(BaseModel):
    """Provide the static historical-universe eligibility setting.

    Example:
        config = BacktestConfig(
            min_listing_calendar_days=120,
        )
    """

    model_config = ConfigDict(extra="forbid")

    min_listing_calendar_days: int = Field(ge=0, strict=True)


class InitialAccount(BaseModel):
    """Describe the account at the exact replay start, including same-day limits.

    Example:
        account = InitialAccount(ts_us=1, trade_date="2026-07-03", cash="20000",
                                 positions={}, sellable={}, average_cost={}, peak_equity=20000)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: int = Field(strict=True)
    trade_date: str
    cash: Decimal = Field(ge=0, allow_inf_nan=False)
    positions: dict[str, NonnegativeQuantity]
    sellable: dict[str, NonnegativeQuantity]
    average_cost: dict[str, NonnegativeMoney]
    peak_equity: float = Field(ge=0, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if self.positions.keys() != self.sellable.keys() or self.positions.keys() != self.average_cost.keys():
            raise ValueError("positions, sellable and average_cost must cover the same symbols")
        if any(self.sellable[symbol] > qty for symbol, qty in self.positions.items()):
            raise ValueError("sellable must not exceed positions")
        return self


class FeeConfig(BaseModel):
    """Explicit scenario charges; values do not claim actual broker fees.

    Example:
        fees = FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=0)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    commission_rate: NonnegativeMoney
    stamp_tax_rate: NonnegativeMoney
    transfer_rate: NonnegativeMoney
    min_commission: NonnegativeMoney


class FakeExecutionConfig(BaseModel):
    """Prepare normalized observations, quantity rules and an explicit simulation end.

    None slippage means the reference scenario; it is rejected by market execution.

    Example:
        config = FakeExecutionConfig(market_ref="/data/market.json", end_ts_us=3,
                                     quantity_rules=rules, fees=fees, annual_factor=252,
                                     slippage_bp=0)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    market_ref: Path
    end_ts_us: int = Field(strict=True)
    quantity_rules: dict[str, QuantityRules]
    fees: FeeConfig
    annual_factor: float = Field(gt=0, allow_inf_nan=False, strict=True)
    slippage_bp: float | None = Field(default=None, ge=0, lt=10000, allow_inf_nan=False, strict=True)
    risk_check_ts_us: tuple[StrictInt, ...] = ()

    @model_validator(mode="after")
    def _validate_market_ref(self) -> Self:
        if not self.market_ref.is_absolute():
            raise ValueError("market_ref must be an absolute file path")
        return self


class SingleNameRiskConfig(BaseModel):
    """Cap each name using the explicitly selected equity ratio.

    Example:
        rule = SingleNameRiskConfig(type="max_single_name", max_ratio=0.2)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["max_single_name"]
    max_ratio: float = Field(ge=0, allow_inf_nan=False, strict=True)


class GrossRiskConfig(BaseModel):
    """Cap current holdings plus possible buys without anticipating sell proceeds.

    Example:
        rule = GrossRiskConfig(type="max_gross_exposure", max_gross_ratio=0.8)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["max_gross_exposure"]
    max_gross_ratio: float = Field(ge=0, allow_inf_nan=False, strict=True)


class DrawdownRiskConfig(BaseModel):
    """Request liquidation when the declared drawdown is reached.

    Example:
        rule = DrawdownRiskConfig(type="max_drawdown", max_drawdown=0.1)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["max_drawdown"]
    max_drawdown: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


RiskRuleConfig: TypeAlias = Annotated[
    SingleNameRiskConfig | GrossRiskConfig | DrawdownRiskConfig, Field(discriminator="type"),
]


class RiskConfig(BaseModel):
    """Select a nonempty ordered rule chain with no business defaults.

    Example:
        risk = RiskConfig(rules=({"type": "max_single_name", "max_ratio": 0.2},))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[RiskRuleConfig, ...] = Field(min_length=1)
