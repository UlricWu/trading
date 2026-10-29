# filepath: src/trading/backtest/reporting/metrics.py
from __future__ import annotations

from decimal import Decimal

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config.backtest_config import BacktestMode, RiskConfig
from src.trading.backtest.market.replay import MarketDataIssue
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.corporate_actions import DistributionEntitlement
from src.trading.backtest.risk_effectiveness import RiskEffectiveness
from src.trading.backtest.evaluation import (
    SignalEvaluation, RiskEvaluation, ExecutionEvaluation, PortfolioEvaluation,
)


class AccountResult(BaseModel):
    """The settled endpoint snapshot, including outstanding order identities.

    Example:
        result = AccountResult(cash="20000", positions={}, sellable={}, average_cost={},
                               trade_date="2026-07-03", equity=20000, pending_order_ids=())
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    cash: Decimal
    positions: dict[str, int]
    sellable: dict[str, int]
    average_cost: dict[str, Decimal]
    trade_date: str
    equity: float
    pending_order_ids: tuple[int, ...]
    dividend_receivable: Decimal = Decimal(0)
    distribution_entitlements: tuple[DistributionEntitlement, ...] = ()
    shares_receivable_value: float = 0.0
    registration_holdings: dict[str, dict[str, int]] = Field(default_factory=dict)
    corporate_action_issues: tuple[CorporateActionIssue, ...] = ()


class MarketDataQuality(BaseModel):
    """Distinguish a diagnostic account path from one without observed source gaps.

    Example:
        quality = MarketDataQuality(diagnostic_only=bool(issues), issues=issues)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    diagnostic_only: bool = False
    issues: tuple[MarketDataIssue, ...] = ()
    corporate_action_issues: tuple[CorporateActionIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_diagnostic_status(self) -> Self:
        if self.diagnostic_only != bool(self.issues or self.corporate_action_issues):
            raise ValueError("diagnostic_only must reflect recorded market or corporate-action issues")
        return self


class CorporateActionSummary(BaseModel):
    """Declare the actual tax basis and coverage, separately from trade statistics.

    Example:
        summary = CorporateActionSummary(distribution_count=10, registered_count=3,
            gross_cash_paid="60", shares_received={"600000": 100})
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_basis: Literal["gross"] = "gross"
    coverage: Literal["distributions_and_supplied_notices"] = "distributions_and_supplied_notices"
    distribution_count: int
    registered_count: int
    gross_cash_paid: Decimal
    shares_received: dict[str, int]
    compensation_floor_count: int = 0


class BacktestMetrics(BaseModel):
    """Only executed evaluations are published; missing capabilities carry explanations.

    Example:
        metrics = BacktestMetrics(mode="signal_eval", parameters=parameters,
                                  not_run={"risk": "no_business_risk"})
    """

    model_config = ConfigDict(extra="forbid")
    mode: BacktestMode
    parameters: dict[str, object]
    not_run: dict[str, str]
    prediction_refs: tuple[str, ...] = ()
    data_quality: MarketDataQuality = MarketDataQuality()
    corporate_actions: CorporateActionSummary | None = None
    signal_eval: SignalEvaluation | None = None
    risk_eval: RiskEvaluation | None = None
    execution_eval: ExecutionEvaluation | None = None
    portfolio: PortfolioEvaluation | None = None
    final_account: AccountResult | None = None
    applied_risk: RiskConfig | None = None
    risk_effectiveness: RiskEffectiveness | None = None
