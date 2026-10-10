# filepath: tests/trading/execution/test_engine.py
from decimal import Decimal
from unittest.mock import Mock

from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.engine import ExecutionOrchestrator
from src.trading.execution.models.cost_a_share import AShareCostModel
from src.trading.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.execution.policies.constraints import AShareTargetClippingPolicy
from src.trading.execution.policies.validation import AShareOrderValidation
from src.trading.execution.settlement import SettlementEngine
from src.trading.execution.venue.sim_immediate import SimImmediateVenue
from src.trading.market.data_view import MarketDataView
from src.trading.portfolio.state import PortfolioState


def test_existing_daily_execution_uses_shared_budget_and_settlement() -> None:
    market = Mock(spec=MarketDataView)
    market.get_price.return_value = 10.0
    account = PortfolioState(2005, 2005, current_day="2026-07-03")
    ledger = ExecutionLedger()
    execution = ExecutionOrchestrator(
        clip_policy=AShareTargetClippingPolicy(), validator=AShareOrderValidation(),
        venue=SimImmediateVenue(FixedBPSlippageModel(0)),
        settlement=SettlementEngine(AShareCostModel(0.0003, 0, 0, 5)),
    )
    fills = execution.execute_targets(ts_us=1, targets={"A": 200}, data_view=market,
                                      state=account, ledger=ledger)
    assert len(fills) == 1
    assert account.cash == Decimal("0")
    assert account.positions == {"A": 200}
    assert ledger.order_costs[1].total == 5
    assert len(ledger.fills_by_id) == 1
