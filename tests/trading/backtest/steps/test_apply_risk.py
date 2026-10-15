# filepath: tests/trading/backtest/steps/test_apply_risk.py
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext
from src.trading.backtest.steps.apply_risk import ApplyRiskStep
from src.trading.core.events import OrderIntent, Side, TargetEvent
from src.trading.core.time import US_PER_MINUTE
from src.trading.risk import RiskManager
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule

M = US_PER_MINUTE


@pytest.mark.parametrize('has_target', [True, False])
def test_risk_retains_original_request_and_records_only_a_local_observation(
    tick_context: BacktestContext, has_target: bool,
) -> None:
    order = OrderIntent(0, 'A', Side.BUY, 1000, 1, 0, 3 * M, Decimal('10'))
    tick_context.executor.submit_orders(orders=(order,))
    original = TargetEvent(M, {'A': 1000}) if has_target else None
    tick_context.tick.original_target = tick_context.tick.execution_target = original
    tick_context.risk = RiskManager((MaxSingleNameNotionalRule(.1),))
    assert ApplyRiskStep().run(tick_context) is tick_context

    observation = tick_context.tick.risk_observation
    assert tick_context.tick.original_target is original
    assert observation.case.target == ({'A': 1000} if has_target else None)
    assert observation.case.context.pending_orders[0].intent is order
    assert observation.decision.adjusted == tick_context.tick.execution_target.targets == {'A': 200}
    assert tick_context.tick.cancel_order_ids == (1,)
    assert observation.outcome is None
    assert tick_context.state.risk_observations == []
    assert tuple(tick_context.state.pending_orders) == (1,)
