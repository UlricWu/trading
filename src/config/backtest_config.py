# filepath: src/config/backtest_config.py
from __future__ import annotations

from enum import Enum
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.data_system.normalize.level2_security import SECURITY_TYPE_RANGES, SecurityType
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.orders.constraints import QuantityRules, QuoteRule
from src.trading.portfolio.corporate_actions import DistributionEntitlement
from src.utils.datetime_utils import DateTimeUtils

NonnegativeQuantity = Annotated[int, Field(strict=True, ge=0)]
NonnegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class BacktestMode(str, Enum):
    """Fixed backtest task modes accepted by a submission.

    Example:
        mode = BacktestMode.FULL_BACKTEST
    """

    SIGNAL_EVAL = "signal_eval"
    STRATEGY_EVAL = "strategy_eval"
    EXECUTION_EVAL = "execution_eval"
    RISK_RULE_EVAL = "risk_rule_eval"
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
    distribution_entitlements: tuple[DistributionEntitlement, ...] = ()
    registration_holdings: dict[str, dict[str, NonnegativeQuantity]] = Field(default_factory=dict)
    corporate_action_issues: tuple[CorporateActionIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if any(issue.ts_us > self.ts_us for issue in self.corporate_action_issues):
            raise ValueError("initial corporate-action issues cannot come from the future")
        for day in self.registration_holdings:
            DateTimeUtils.require_system_date(day, field_name="registration_holdings")
            if DateTimeUtils.local_time_to_utc_epoch_us(time(15), date.fromisoformat(day)) > self.ts_us:
                raise ValueError("registration holdings cannot include future closes")
        if self.positions.keys() != self.sellable.keys() or self.positions.keys() != self.average_cost.keys():
            raise ValueError("positions, sellable and average_cost must cover the same symbols")
        if any(self.sellable[symbol] > qty for symbol, qty in self.positions.items()):
            raise ValueError("sellable must not exceed positions")
        ids = [item.distribution.action_id for item in self.distribution_entitlements]
        if len(ids) != len(set(ids)):
            raise ValueError("initial distribution entitlements must have unique action_id")
        for item in self.distribution_entitlements:
            action = item.distribution
            if max(action.record_ts_us, action.announced_ts_us) > self.ts_us:
                raise ValueError("initial entitlement cannot precede its registration or current terms")
            if action.kind == "compensation":
                if item.shares_credited and (action.shares_credit_ts_us is None or action.shares_credit_ts_us > self.ts_us):
                    raise ValueError("initial compensation credit cannot precede its known date")
                if item.shares_released and (action.shares_available_ts_us is None or action.shares_available_ts_us > self.ts_us):
                    raise ValueError("initial compensation release cannot precede availability")
                if item.cash_paid and (action.pay_ts_us is None or action.pay_ts_us > self.ts_us):
                    raise ValueError("initial compensation payment cannot precede its known date")
                continue
            if item.recognition_issue is not None and (action.ex_ts_us is None or action.ex_ts_us > self.ts_us):
                raise ValueError("initial recognition issue cannot precede the ex date")
            if item.recognized != (action.ex_ts_us is not None and action.ex_ts_us <= self.ts_us
                and action.benefit_state == "confirmed" and item.recognition_issue is None):
                raise ValueError("initial entitlement recognition must match the snapshot time")
            if item.recognition_issue is not None:
                continue
            if action.pay_ts_us is not None and item.cash_paid != (action.pay_ts_us <= self.ts_us):
                raise ValueError("initial dividend payment must match the snapshot time")
            if action.shares_available_ts_us is not None and item.shares_released != (action.shares_available_ts_us <= self.ts_us):
                raise ValueError("initial distributed-share release must match the snapshot time")
        locked: dict[str, int] = {}
        for item in self.distribution_entitlements:
            if item.recognized and not item.shares_released and (item.distribution.kind == "distribution" or item.shares_credited):
                symbol = item.distribution.symbol
                locked[symbol] = locked.get(symbol, 0) + item.shares_qty
        if any(qty > self.positions.get(symbol, 0) - self.sellable.get(symbol, 0) for symbol, qty in locked.items()):
            raise ValueError("initial sellable must exclude all unlisted distributed shares")
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


class QuotedFillConfig(BaseModel):
    """Fill accepted orders in full at their quotes when they become active.

    Example:
        fill = QuotedFillConfig(type="quoted")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["quoted"]


class LimitPriceFillConfig(BaseModel):
    """Apply explicit slippage, order quotes and available observation volume.

    Example:
        fill = LimitPriceFillConfig(type="limit_price", slippage_bp=0)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["limit_price"]
    slippage_bp: float = Field(ge=0, lt=10000, allow_inf_nan=False, strict=True)


FillConfig: TypeAlias = Annotated[
    QuotedFillConfig | LimitPriceFillConfig, Field(discriminator="type"),
]


class ReplayMarketSourceConfig(BaseModel):
    """Select formal stock facts to prepare within the backtest startup Steps.

    Coverage dates are independent of decisions and the simulation endpoint.
    The replay consumes local facts prepared in memory.

    Example:
        source = ReplayMarketSourceConfig(frequency="minute", start="2026-07-17",
            end="2026-07-21", symbols=("000001", "600000"))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    frequency: Literal["daily", "minute"]
    start: str
    end: str
    symbols: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        DateTimeUtils.require_system_date(self.start, field_name="market_source.start")
        DateTimeUtils.require_system_date(self.end, field_name="market_source.end")
        if self.start > self.end:
            raise ValueError("market_source.start must not follow market_source.end")
        stock_ranges = tuple(rule for rule in SECURITY_TYPE_RANGES if rule.security_type == SecurityType.STOCK)
        if any(len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
               or not any(f"{rule.start:06d}" <= symbol <= f"{rule.end:06d}" for rule in stock_ranges)
               for symbol in self.symbols):
            raise ValueError("market_source.symbols must be supported six-digit Shanghai/Shenzhen stock codes")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("market_source.symbols must be unique")
        return self


class QuantityRulesConfig(BaseModel):
    """Declare common execution rules and complete replacements for selected symbols.

    Example:
        rules = QuantityRulesConfig(default=common_rule, overrides={"688001": star_rule})
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    default: QuantityRules
    overrides: dict[str, QuantityRules] = Field(default_factory=dict)


class FakeExecutionConfig(BaseModel):
    """Select local market data, quantity rules and an explicit simulation end.

    Example:
        source = ReplayMarketSourceConfig(frequency="daily", start="2026-07-17",
            end="2026-07-21", symbols=("600000",))
        config = FakeExecutionConfig(market_source=source, end_ts_us=1784617200000000,
            quantity_rules=rules, fees=fees, annual_factor=252,
            fill=LimitPriceFillConfig(type="limit_price", slippage_bp=0))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    market_source: ReplayMarketSourceConfig
    end_ts_us: int = Field(strict=True)
    quantity_rules: QuantityRulesConfig
    fees: FeeConfig
    annual_factor: float = Field(gt=0, allow_inf_nan=False, strict=True)
    fill: FillConfig
    risk_check_ts_us: tuple[StrictInt, ...] = ()


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
