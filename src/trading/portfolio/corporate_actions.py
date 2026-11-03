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

    Reference parameters retain their announcement or ordinary-formula assumption basis.

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
    ex_ts_us: StrictInt | None
    kind: Literal["distribution", "compensation"] = "distribution"
    benefit_state: Literal["conditional", "confirmed", "cancelled"] = "confirmed"
    shares_credit_ts_us: StrictInt | None = None
    shares_numerator: StrictInt | None = Field(default=None, ge=0)
    shares_denominator: StrictInt | None = Field(default=None, gt=0)
    pay_ts_us: StrictInt | None = None
    shares_available_ts_us: StrictInt | None = None
    cash_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    shares_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_cash_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_shares_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    reference_basis: Literal["announcement", "ordinary_formula_assumption"] = "announcement"

    @model_validator(mode="after")
    def _validate_dates(self) -> Self:
        if (self.shares_numerator is None) != (self.shares_denominator is None):
            raise ValueError("share allocation requires both numerator and denominator")
        if ((self.reference_cash_per_share is None) != (self.reference_shares_per_share is None)
            or (self.reference_price is not None and self.reference_cash_per_share is not None)):
            raise ValueError("reference requires a complete formula pair or an absolute price")
        if self.reference_basis == "ordinary_formula_assumption" and (
            self.cash_per_share is None or self.shares_per_share is None
            or self.reference_price is not None or self.reference_cash_per_share != self.cash_per_share
            or self.reference_shares_per_share != self.shares_per_share
        ):
            raise ValueError("ordinary reference assumption must use the distribution cash and share ratios")
        if self.kind == "compensation":
            if self.reference_basis != "announcement":
                raise ValueError("compensation cannot use an ordinary ex-price assumption")
            if self.shares_numerator is not None and self.shares_per_share is None:
                raise ValueError("a known share formula requires known share terms")
            if any(stamp is not None and stamp < self.record_ts_us for stamp in
                (self.pay_ts_us, self.shares_credit_ts_us, self.shares_available_ts_us)):
                raise ValueError("compensation settlement cannot precede eligibility")
            return self
        if self.shares_credit_ts_us is not None:
            raise ValueError("ordinary distribution shares are allocated at recognition")
        if self.ex_ts_us is not None and self.record_ts_us >= self.ex_ts_us:
            raise ValueError("distribution requires registration < ex date")
        for amount, timestamp, field in (
            (self.cash_per_share, self.pay_ts_us, "pay_ts_us"),
            (self.shares_per_share, self.shares_available_ts_us, "shares_available_ts_us"),
        ):
            if timestamp is not None and (amount == 0 or (self.ex_ts_us is not None and timestamp < self.ex_ts_us)):
                raise ValueError(f"{field} must follow a known ex date and cannot settle a zero benefit")
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
    shares_credited: bool = False
    actual_shares_qty: StrictInt | None = Field(default=None, ge=0)
    recognition_issue: Literal["fractional_shares", "pending_distribution_terms"] | None = None

    @model_validator(mode="after")
    def _validate_entitlement(self) -> Self:
        pending = self.distribution.cash_per_share is None or self.distribution.shares_per_share is None
        shares = None if self.distribution.shares_per_share is None else self.theoretical_shares_qty
        if self.recognition_issue == "pending_distribution_terms" and (self.recognized or not pending):
            raise ValueError("pending distribution issue requires an unrecognized amount not yet known")
        if self.recognition_issue == "fractional_shares" and (self.recognized or shares is None
            or shares == shares.to_integral_value()):
            raise ValueError("fractional-share recognition issue requires an unrecognized fractional allocation")
        if self.recognized and pending:
            raise ValueError("recognized distribution amounts must be known")
        if self.recognized and self.distribution.benefit_state != "confirmed":
            raise ValueError("recognized compensation requires confirmed benefits")
        if self.recognized and self.distribution.kind == "distribution" and shares is not None and shares != shares.to_integral_value():
            raise ValueError("fractional distributed shares require an explicit allocation result")
        if (self.cash_paid or self.shares_released) and not self.recognized:
            raise ValueError("settled benefits require a recognized entitlement")
        if self.shares_credited and not self.recognized:
            raise ValueError("credited shares require a recognized entitlement")
        if self.distribution.kind == "compensation" and self.shares_released and not self.shares_credited:
            raise ValueError("compensation shares cannot trade before credit")
        if self.actual_shares_qty is not None and self.distribution.kind != "compensation":
            raise ValueError("account-specific allocation is only supported for compensation")
        return self

    @property
    def cash_amount(self) -> Decimal:
        """Return the exact gross cash right, independently of current holdings.

        Example:
            amount = entitlement.cash_amount
        """
        if self.distribution.cash_per_share is None:
            raise ValueError("distribution cash amount is not yet known")
        return self.eligible_qty * self.distribution.cash_per_share

    @property
    def shares_qty(self) -> int:
        """Return an integral allocation after the applicable terms are established.

        Example:
            shares = entitlement.shares_qty
        """
        if self.actual_shares_qty is not None:
            return self.actual_shares_qty
        if self.distribution.shares_per_share is None:
            raise ValueError("distribution share ratio is not yet known")
        if self.distribution.kind == "compensation":
            if self.distribution.shares_numerator is not None:
                return self.eligible_qty * self.distribution.shares_numerator // cast(int, self.distribution.shares_denominator)
            return int(self.theoretical_shares_qty)
        shares = self.theoretical_shares_qty
        if shares != shares.to_integral_value():
            raise ValueError("fractional distributed shares require an explicit allocation result")
        return int(shares)

    @property
    def theoretical_shares_qty(self) -> Decimal:
        """Calculate the sourced allocation before account settlement rounding.

        Example:
            theoretical = entitlement.theoretical_shares_qty
        """
        action = self.distribution
        if action.shares_numerator is not None:
            return Decimal(self.eligible_qty * action.shares_numerator) / Decimal(cast(int, action.shares_denominator))
        if action.shares_per_share is None:
            raise ValueError("distribution share ratio is not yet known")
        return self.eligible_qty * action.shares_per_share


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
    event: Literal["terms_updated", "reference_updated", "registered", "recognized", "entitlement_revised", "cash_paid", "shares_credited", "shares_released"]
    eligible_qty: int
    cash_delta: Decimal
    receivable_delta: Decimal
    shares_delta: int
    source_ref: str
    shares_receivable_delta: int = 0
    fractional_shares: Decimal = Decimal(0)
    allocation_basis: Literal["exact", "floor_assumption", "account_result"] = "exact"


def recognize_distribution(
    account: PortfolioState, entitlement: DistributionEntitlement, *, ts_us: int,
) -> CorporateActionRecord:
    """Recognize cash rights and allocate shares while preserving total position cost.

    Example:
        record = recognize_distribution(account, account.distribution_entitlements[action.action_id], ts_us=now_us)
    """
    action = entitlement.distribution
    shares = entitlement.shares_qty
    cash = entitlement.cash_amount
    if shares and action.kind == "distribution":
        old_qty = account.positions.get(action.symbol, 0)
        total_cost = account.avg_cost.get(action.symbol, Decimal(0)) * old_qty
        account.positions[action.symbol] = old_qty + shares
        account.avg_cost[action.symbol] = total_cost / (old_qty + shares)
    account.distribution_entitlements[action.action_id] = entitlement.model_copy(
        update={"recognized": True, "recognition_issue": None})
    if action.kind == "compensation":
        return CorporateActionRecord(action.action_id, ts_us, action.symbol, "recognized",
            entitlement.eligible_qty, Decimal(0), cash, 0, action.source_ref, shares,
            entitlement.theoretical_shares_qty - shares,
            "account_result" if entitlement.actual_shares_qty is not None
            else "exact" if entitlement.theoretical_shares_qty == shares else "floor_assumption")
    return CorporateActionRecord(action.action_id, ts_us, action.symbol, "recognized",
        entitlement.eligible_qty, Decimal(0), cash, shares, action.source_ref)


def credit_compensation_shares(
    account: PortfolioState, entitlement: DistributionEntitlement, *, ts_us: int,
) -> CorporateActionRecord:
    """Move confirmed compensation shares from receivables into locked holdings.

    Example:
        record = credit_compensation_shares(account, entitlement, ts_us=credit_us)
    """
    action = entitlement.distribution
    shares = entitlement.shares_qty
    if shares:
        old_qty = account.positions.get(action.symbol, 0)
        total_cost = account.avg_cost.get(action.symbol, Decimal(0)) * old_qty
        account.positions[action.symbol] = old_qty + shares
        account.avg_cost[action.symbol] = total_cost / (old_qty + shares)
    account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"shares_credited": True})
    return CorporateActionRecord(action.action_id, ts_us, action.symbol, "shares_credited",
        entitlement.eligible_qty, Decimal(0), Decimal(0), shares, action.source_ref, -shares)


def pay_dividend(account: PortfolioState, entitlement: DistributionEntitlement, *, ts_us: int) -> CorporateActionRecord:
    """Move an existing receivable into cash without recognizing income twice.

    Example:
        record = pay_dividend(account, account.distribution_entitlements[action.action_id], ts_us=now_us)
    """
    action = entitlement.distribution
    account.cash += entitlement.cash_amount
    account.realized_pnl += entitlement.cash_amount
    account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"cash_paid": True})
    return CorporateActionRecord(action.action_id, ts_us, action.symbol, "cash_paid",
        entitlement.eligible_qty, entitlement.cash_amount, -entitlement.cash_amount, 0, action.source_ref)
