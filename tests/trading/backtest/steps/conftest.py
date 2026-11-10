# filepath: tests/trading/backtest/steps/conftest.py
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState, TickState
from src.trading.backtest.execution.fill_models import LimitPriceFillModel
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.core.time import US_PER_MINUTE
from src.trading.orders.constraints import QuantityRules, QuoteRule
from src.trading.portfolio.state import PortfolioState

M = US_PER_MINUTE


@pytest.fixture
def state() -> BacktestState:
    return BacktestState(PortfolioState(20000, 20000, current_day='2026-07-03'))


@pytest.fixture
def market_input() -> ReplayMarketInput:
    return ReplayMarketInput.model_validate({
        'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'close_ts_us': 4 * M,
                      'windows': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'end_ts_us': 4 * M}]}],
        'marks': [{'ts_us': ts, 'trade_date': '2026-07-03', 'prices': {'A': '10', 'B': '10'}, 'kind': kind}
                  for ts, kind in ((0, 'mark'), (4 * M, 'daily_close'))],
        'observations': [],
    })


@pytest.fixture
def executor(state: BacktestState) -> AccountExecutionSimulator:
    return AccountExecutionSimulator(
        portfolio=state.portfolio_state, ledger=state.execution_ledger, pending_orders=state.pending_orders,
        settlement=SettlementEngine(AShareCostModel(0, 0, 0, 10)),
        fill_model=LimitPriceFillModel(FixedBPSlippageModel(0)),
        rules={symbol: QuantityRules(1, 1, 1, 1, 1, Decimal('0.01'), True) for symbol in ('A', 'B')},
        quote_rule=QuoteRule(Decimal(0), Decimal(0)),
    )


@pytest.fixture
def tick_context(state: BacktestState, market_input: ReplayMarketInput, executor: AccountExecutionSimulator) -> BacktestContext:
    market = ReplayMarket(market_input)
    market.on_time(M)
    state.initial_equity = 20000
    state.equity_curve.starting_peak_equity = 20000
    state.equity_curve.append(ts_us=M, cash=20000, market_value=0)
    return BacktestContext(metrics=BacktestMetrics(mode='full_backtest', parameters={}, not_run={}),
        inputs=BacktestInputs(market_input, {}), state=state, market=market, executor=executor,
        tick=TickState(ts_us=M, session=market_input.session_at(M)))
