# filepath: src/trading/backtest/reporting/metrics.py
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from src.config.backtest_config import BacktestMode
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


class BacktestMetrics(BaseModel):
    """Only executed evaluations are published; missing capabilities carry explanations.

    Example:
        metrics = BacktestMetrics(mode="signal_eval", parameters=parameters,
                                  not_run={"execution": "signal evaluation"}, signal_eval=evaluation)
    """

    model_config = ConfigDict(extra="forbid")
    mode: BacktestMode
    parameters: dict[str, object]
    not_run: dict[str, str]
    input_sha256: dict[str, str] = Field(default_factory=dict)
    prediction_refs: tuple[str, ...] = ()
    signal_eval: SignalEvaluation | None = None
    risk_eval: RiskEvaluation | None = None
    execution_eval: ExecutionEvaluation | None = None
    portfolio: PortfolioEvaluation | None = None
    final_account: AccountResult | None = None
