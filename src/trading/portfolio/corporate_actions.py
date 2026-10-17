# filepath: src/trading/portfolio/corporate_actions.py
"""Non-trade distributions and the account's outstanding entitlements."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self, TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

if TYPE_CHECKING:
    from src.trading.portfolio.state import PortfolioState


class Distribution(BaseModel):
    """An implemented distribution with explicit replay availability and settlement times.

    Example:
        action = Distribution(action_id="dividend/600000/2026", symbol="600000",
            source_ref="dividend/v1/2026-09-02", announced_ts_us=1, record_ts_us=2,
            ex_ts_us=3, pay_ts_us=4, cash_per_share="0.10", shares_per_share="0",
            reference_cash_per_share="0.10", reference_shares_per_share="0")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    announced_ts_us: StrictInt
    record_ts_us: StrictInt
    ex_ts_us: StrictInt
    pay_ts_us: StrictInt | None = None
    shares_available_ts_us: StrictInt | None = None
    cash_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    shares_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    reference_cash_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    reference_shares_per_share: Decimal = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_dates(self) -> Self:
        if self.record_ts_us >= self.ex_ts_us or self.announced_ts_us > self.ex_ts_us:
            raise ValueError("distribution requires registration < ex date and terms available by ex date")
        for amount, timestamp, field in (
            (self.cash_per_share, self.pay_ts_us, "pay_ts_us"),
            (self.shares_per_share, self.shares_available_ts_us, "shares_available_ts_us"),
        ):
            if bool(amount) != (timestamp is not None) or (timestamp is not None and timestamp < self.ex_ts_us):
                raise ValueError(f"{field} must follow ex date and exist exactly when that benefit is nonzero")
        return self


class DistributionEntitlement(BaseModel):
    """Registration survives sales until the cash and distributed shares have settled.

    Example:
        entitlement = DistributionEntitlement(distribution=action, eligible_qty=200)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    distribution: Distribution
    eligible_qty: int = Field(gt=0, strict=True)
    recognized: bool = False
    cash_paid: bool = False
    shares_released: bool = False
    recognition_issue: Literal["fractional_shares"] | None = None

    @model_validator(mode="after")
    def _validate_entitlement(self) -> Self:
        shares = self.eligible_qty * self.distribution.shares_per_share
        if self.recognition_issue is not None and (self.recognized or shares == shares.to_integral_value()):
            raise ValueError("fractional-share recognition issue requires an unrecognized fractional allocation")
        if self.recognized and shares != shares.to_integral_value():
            raise ValueError("fractional distributed shares require an explicit allocation result")
        if (self.cash_paid or self.shares_released) and not self.recognized:
            raise ValueError("settled benefits require a recognized entitlement")
        return self

    @property
    def cash_amount(self) -> Decimal:
        """Return the exact gross cash right, independently of current holdings.

        Example:
            amount = entitlement.cash_amount
        """
        return self.eligible_qty * self.distribution.cash_per_share

    @property
    def shares_qty(self) -> int:
        """Return an integral allocation after the applicable terms are established.

        Example:
            shares = entitlement.shares_qty
        """
        shares = self.eligible_qty * self.distribution.shares_per_share
        if shares != shares.to_integral_value():
            raise ValueError("fractional distributed shares require an explicit allocation result")
        return int(shares)


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    """An account event that must never be counted as an order or fill.

    Example:
        record = CorporateActionRecord("dividend/600000/2026", 2, "600000",
            "registered", 200, Decimal(0), Decimal(0), 0, "dividend/v1/2026-09-02")
    """

    action_id: str
    ts_us: int
    symbol: str
    event: Literal["terms_updated", "registered", "recognized", "cash_paid", "shares_released"]
    eligible_qty: int
    cash_delta: Decimal
    receivable_delta: Decimal
    shares_delta: int
    source_ref: str


def recognize_distribution(account: PortfolioState, entitlement: DistributionEntitlement) -> CorporateActionRecord:
    """Recognize cash rights and allocate shares while preserving total position cost.

    Example:
        record = recognize_distribution(account, account.distribution_entitlements[action.action_id])
    """
    action = entitlement.distribution
    shares = entitlement.shares_qty
    if shares:
        old_qty = account.positions.get(action.symbol, 0)
        total_cost = account.avg_cost.get(action.symbol, Decimal(0)) * old_qty
        account.positions[action.symbol] = old_qty + shares
        account.avg_cost[action.symbol] = total_cost / (old_qty + shares)
    account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"recognized": True})
    return CorporateActionRecord(action.action_id, action.ex_ts_us, action.symbol, "recognized",
        entitlement.eligible_qty, Decimal(0), entitlement.cash_amount, shares, action.source_ref)


def pay_dividend(account: PortfolioState, entitlement: DistributionEntitlement) -> CorporateActionRecord:
    """Move an existing receivable into cash without recognizing income twice.

    Example:
        record = pay_dividend(account, account.distribution_entitlements[action.action_id])
    """
    action = entitlement.distribution
    account.cash += entitlement.cash_amount
    account.realized_pnl += entitlement.cash_amount
    account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"cash_paid": True})
    return CorporateActionRecord(action.action_id, cast(int, action.pay_ts_us), action.symbol, "cash_paid",
        entitlement.eligible_qty, entitlement.cash_amount, -entitlement.cash_amount, 0, action.source_ref)
