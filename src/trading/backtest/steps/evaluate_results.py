# filepath: src/trading/backtest/steps/evaluate_results.py
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.config.backtest_config import RiskConfig
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState
from src.trading.backtest.evaluation import evaluate_execution, evaluate_portfolio, evaluate_risk, evaluate_signals
from src.trading.backtest.reporting.metrics import AccountResult
from src.trading.backtest.risk_effectiveness import evaluate_risk_effectiveness
from src.training.evaluation import evaluate_prediction_records
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class EvaluateResultsStep:
    """Derive supported evaluations only after original trading facts have been saved.

    Example:
        context = EvaluateResultsStep(pm=pm, annual_factor=252, risk=None).run(context)
    """

    pm: PathManager
    annual_factor: float
    risk: RiskConfig | None

    def run(self, context: BacktestContext) -> BacktestContext:
        """Read labels here and summarize the completed account without replaying decisions.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        metrics = context.metrics
        if not isinstance(cast(BacktestInputs, context.inputs).decisions, OrderCommandSchedule):
            observations = evaluate_prediction_records(state.prediction_refs, pm=self.pm)
            metrics.signal_eval = evaluate_signals(observations, decision_dates=state.decision_dates)
        if self.risk is not None:
            metrics.risk_eval = evaluate_risk(state.risk_observations,
                configured_rules=tuple(rule.type for rule in self.risk.rules))
            metrics.risk_effectiveness = evaluate_risk_effectiveness(state.risk_observations,
                config=self.risk, ledger=state.execution_ledger)
        metrics.execution_eval = evaluate_execution(state.execution_ledger)
        metrics.portfolio = evaluate_portfolio(state.equity_curve,
            initial_equity=cast(float, state.initial_equity), daily_equity=state.daily_equity,
            ledger=state.execution_ledger, annual_factor=self.annual_factor)
        account = state.portfolio_state
        metrics.final_account = AccountResult(cash=account.cash, positions=account.positions,
            sellable={symbol: account.t1_sellable(symbol) for symbol in account.positions},
            average_cost=account.avg_cost, trade_date=cast(str, account.current_day),
            equity=state.equity_curve.points[-1].equity, pending_order_ids=tuple(state.pending_orders))
        metrics.prediction_refs = tuple(str(ref) for ref in state.prediction_refs)
        return context
