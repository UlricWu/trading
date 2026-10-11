# filepath: src/trading/backtest/reporting/metrics.py
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from src.config.backtest_config import BacktestMode, RiskConfig
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


class RiskComparison(BaseModel):
    """Compare portfolios from the same inputs; every delta is with minus without risk.

    Example:
        comparison = RiskComparison(without_risk=baseline, with_risk=treatment,
            net_profit_delta=100, total_return_delta=0.01, max_drawdown_delta=-0.02,
            total_fees_delta="0", turnover_delta=-0.1)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    without_risk: PortfolioEvaluation
    with_risk: PortfolioEvaluation
    net_profit_delta: float
    total_return_delta: float | None
    max_drawdown_delta: float
    total_fees_delta: Decimal
    turnover_delta: float | None


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
    signal_eval: SignalEvaluation | None = None
    risk_eval: RiskEvaluation | None = None
    execution_eval: ExecutionEvaluation | None = None
    portfolio: PortfolioEvaluation | None = None
    final_account: AccountResult | None = None
    applied_risk: RiskConfig | None = None
    risk_comparison: RiskComparison | None = None
