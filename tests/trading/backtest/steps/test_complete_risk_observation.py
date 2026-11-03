# filepath: tests/trading/backtest/steps/test_complete_risk_observation.py
from decimal import Decimal

from src.trading.backtest.context import BacktestContext
from src.trading.backtest.steps.complete_risk_observation import CompleteRiskObservationStep
from src.trading.risk.base import RiskContext, RiskDecision
from src.trading.risk.evaluation import RiskCase, RiskObservation


def test_completion_preserves_case_and_decision_and_freezes_the_valued_outcome(tick_context: BacktestContext) -> None:
    ts_us = tick_context.tick.ts_us
    case = RiskCase(str(ts_us), '2026-07-03', {'A': 1000},
                    RiskContext(ts_us, {'A': 10.0}, 19990, 18990, 20000, {'A': 100}, (), {'A': 1}))
    decision = RiskDecision({'A': 199}, (), 'adjusted', ())
    observation = RiskObservation(case, decision)
    tick_context.tick.risk_observation = observation
    account = tick_context.state.portfolio_state
    account.cash = Decimal('17990')
    account.positions['A'] = 199
    tick_context.state.equity_curve.append(ts_us=ts_us, cash=17990, market_value=1990)
    assert CompleteRiskObservationStep().run(tick_context) is tick_context

    completed, = tick_context.state.risk_observations
    assert completed.case is case and completed.decision is decision
    assert observation.outcome is None
    assert completed.outcome.equity == 19980 and completed.outcome.cash == 17990
    assert completed.outcome.peak_equity == 20000 and completed.outcome.positions == {'A': 199}
    account.positions.clear()
    assert case.context.positions == {'A': 100}
    assert completed.outcome.positions == {'A': 199}
