# filepath: src/trading/backtest/reporting/metrics.py
from __future__ import annotations

from decimal import Decimal

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from src.config.backtest_config import BacktestMode, RiskConfig
from src.trading.backtest.market.replay import MarketDataIssue
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


class MarketDataQuality(BaseModel):
    """Distinguish a diagnostic account path from one without observed source gaps.

    Example:
        quality = MarketDataQuality(diagnostic_only=bool(issues), issues=issues)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    diagnostic_only: bool = False
    issues: tuple[MarketDataIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_diagnostic_status(self) -> Self:
        if self.diagnostic_only != bool(self.issues):
            raise ValueError("diagnostic_only must reflect recorded market data issues")
        return self


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
    signal_eval: SignalEvaluation | None = None
    risk_eval: RiskEvaluation | None = None
    execution_eval: ExecutionEvaluation | None = None
    portfolio: PortfolioEvaluation | None = None
    final_account: AccountResult | None = None
    applied_risk: RiskConfig | None = None
    risk_effectiveness: RiskEffectiveness | None = None
