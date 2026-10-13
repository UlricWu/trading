# filepath: src/trading/backtest/steps/evaluation.py
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.config.backtest_config import RiskConfig
from src.trading.backtest.context import BacktestResultContext
from src.trading.backtest.evaluation import evaluate_execution, evaluate_portfolio, evaluate_risk, evaluate_signals
from src.trading.backtest.reporting.metrics import AccountResult
from src.trading.backtest.risk_effectiveness import evaluate_risk_effectiveness
from src.training.evaluation import evaluate_prediction_records
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class SignalEvaluationStep:
    """Evaluate actual fixed predictions after their production or completed replay.

    Example:
        context = SignalEvaluationStep(pm).run(context)
    """

    pm: PathManager

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Read labels and summarize the shared model-target observations by decision day.

        Example:
            context = step.run(context)
        """
        observations = evaluate_prediction_records(context.prediction_refs, pm=self.pm)
        context.metrics.signal_eval = evaluate_signals(observations, decision_dates=context.decision_dates)
        context.metrics.prediction_refs = tuple(str(ref) for ref in context.prediction_refs)
        return context


@dataclass(frozen=True, slots=True)
class RiskMetricsStep:
    """Summarize rule observations from the completed trading path.

    Example:
        context = RiskMetricsStep(config=risk_config).run(context)
    """

    config: RiskConfig

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Preserve checked, triggered and undefined results without reapplying risk.

        Example:
            context = step.run(context)
        """
        context.metrics.risk_eval = evaluate_risk(context.risk_observations,
            configured_rules=tuple(rule.type for rule in self.config.rules))
        context.metrics.risk_effectiveness = evaluate_risk_effectiveness(context.risk_observations,
            config=self.config, ledger=context.state.execution_ledger)
        return context


class ExecutionEvaluationStep:
    """Evaluate execution outcomes from the single settled ledger.

    Example:
        context = ExecutionEvaluationStep().run(context)
    """

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Aggregate actual orders, fills, costs and terminal reasons.

        Example:
            context = step.run(context)
        """
        context.metrics.execution_eval = evaluate_execution(context.state.execution_ledger)
        return context


@dataclass(frozen=True, slots=True)
class PortfolioEvaluationStep:
    """Evaluate strategy returns from the completed account's official daily samples.

    Example:
        context = PortfolioEvaluationStep(annual_factor=252).run(context)
    """

    annual_factor: float

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Keep return sampling independent of replay wakeups and decision frequency.

        Example:
            context = step.run(context)
        """
        state = context.state
        context.metrics.portfolio = evaluate_portfolio(state.equity_curve,
            initial_equity=cast(float, state.initial_equity), daily_equity=state.daily_equity,
            ledger=state.execution_ledger, annual_factor=self.annual_factor)
        return context


class BacktestSummaryStep:
    """Project the completed account and actual prediction references into the report.

    Example:
        context = BacktestSummaryStep().run(context)
    """

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Capture settled balances, sellable quantities and remaining order identities.

        Example:
            context = step.run(context)
        """
        state = context.state
        account = state.portfolio_state
        context.metrics.final_account = AccountResult(cash=account.cash, positions=account.positions,
            sellable={symbol: account.t1_sellable(symbol) for symbol in account.positions},
            average_cost=account.avg_cost, trade_date=cast(str, account.current_day),
            equity=state.equity_curve.points[-1].equity, pending_order_ids=tuple(state.pending_orders))
        context.metrics.prediction_refs = tuple(str(ref) for ref in state.prediction_refs)
        return context
