# filepath: src/trading/backtest/steps/complete_risk_observation.py
from dataclasses import replace
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.risk.evaluation import RiskObservation


class CompleteRiskObservationStep:
    """Publish a completed observation after submission and the second valuation.

    Example:
        context = CompleteRiskObservationStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Preserve the original case and decision while adding the actual account outcome.

        Example:
            context = step.run(context)
        """
        observation = cast(RiskObservation, cast(TickState, context.tick).risk_observation)
        state = cast(BacktestState, context.state)
        account = state.portfolio_state
        equity = state.equity_curve.points[-1]
        outcome = replace(observation.case.context,
            equity=equity.equity, cash=float(account.cash), peak_equity=equity.peak_equity,
            positions=account.positions, pending_orders=tuple(state.pending_orders.values()))
        state.risk_observations.append(replace(observation, outcome=outcome))
        return context
