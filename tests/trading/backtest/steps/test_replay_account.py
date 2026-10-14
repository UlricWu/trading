# filepath: tests/trading/backtest/steps/test_replay_account.py
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import logs
from src.config.backtest_config import (
    FakeExecutionConfig, FeeConfig, InitialAccount, LimitPriceFillConfig, QuotedFillConfig,
    RiskConfig, ThresholdStrategyConfig,
)
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.replay_account import ReplayAccountStep
from src.trading.core.events import CancelOrders, OrderIntent, OrderStatus, Side, SignalEvent
from src.trading.orders.constraints import QuantityRules, QuoteRule


@pytest.fixture
def market_input() -> ReplayMarketInput:
    return ReplayMarketInput.model_validate({
        'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'close_ts_us': 180_000_000,
                      'windows': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'end_ts_us': 180_000_000}]}],
        'marks': [{'ts_us': ts, 'trade_date': '2026-07-03', 'prices': {'A': '10', 'B': '10'}, 'kind': kind}
                  for ts, kind in ((1, 'mark'), (180_000_000, 'daily_close'))],
        'observations': [],
    })


@pytest.fixture
def replay_step(tmp_path: Path) -> ReplayAccountStep:
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='2000', positions={'B': 100},
        sellable={'B': 60}, average_cost={'B': '10'}, peak_equity=3000)
    config = FakeExecutionConfig(
        market_ref=tmp_path / 'market.json', end_ts_us=180_000_000,
        quantity_rules={s: QuantityRules(1, 1, 1, 1, 1, Decimal('0.01'), True) for s in ('A', 'B')},
        fees=FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=0),
        annual_factor=252, fill=QuotedFillConfig(type='quoted'),
    )
    strategy = ThresholdStrategyConfig(type='threshold', params={'threshold': .5, 'target_quantity': 100},
        order_window_minutes=2, quote=QuoteRule(Decimal(0), Decimal(0)))
    return ReplayAccountStep(initial_account=initial, execution=config, strategy=strategy, risk=None,
                             prediction_dir=tmp_path / 'predictions', progress_clock=lambda: 0)


def _context(inputs: BacktestInputs) -> BacktestContext:
    return BacktestContext(inputs=inputs, metrics=BacktestMetrics(mode='strategy_eval', parameters={}, not_run={}))


def test_each_run_owns_account_progress_and_records_only_current_signals(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput,
) -> None:
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A', 'B'), Path('/prediction'))
    inputs = BacktestInputs(market=market_input, decisions={1: batch})
    first, second = _context(inputs), _context(inputs)
    assert replay_step.run(first) is first
    replay_step.run(second)

    assert first.state is not second.state
    assert first.state.portfolio_state.positions == second.state.portfolio_state.positions == {'A': 100, 'B': 100}
    assert first.state.portfolio_state.cash == second.state.portfolio_state.cash == 1000
    assert first.state.signal_tape.events == [batch.event]
    assert first.state.prediction_refs == [batch.prediction_ref]
    assert first.state.decision_dates == {1: '2026-07-03'}
    assert [event.targets for event in first.state.target_tape.events] == [{'A': 100, 'B': 100}]
    assert [point.ts_us for point in first.state.equity_curve.points] == [1, 180_000_000]
    first.state.portfolio_state.positions['B'] = 200
    first.state.decision_dates.clear()
    assert second.state.portfolio_state.positions['B'] == replay_step.initial_account.positions['B'] == 100
    assert second.state.portfolio_state.t1_sellable('B') == 60
    assert second.state.decision_dates == {1: '2026-07-03'}
    assert second.state.equity_curve.starting_peak_equity == 3000
    assert second.metrics.applied_risk is None
    assert second.metrics.not_run == {'risk': 'no_business_risk', 'price_feasibility': 'quoted_fills',
                                       'market_liquidity': 'quoted_fills'}


def test_dynamic_order_expiry_wakes_account_without_repeating_signal_or_target(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput,
) -> None:
    config = replay_step.execution.model_copy(update={'fill': LimitPriceFillConfig(type='limit_price', slippage_bp=0)})
    step = ReplayAccountStep(initial_account=replay_step.initial_account, execution=config,
        strategy=replay_step.strategy, risk=None, prediction_dir=replay_step.prediction_dir, progress_clock=lambda: 0)
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A', 'B'), Path('/prediction'))
    context = step.run(_context(BacktestInputs(market=market_input, decisions={1: batch})))

    assert [point.ts_us for point in context.state.equity_curve.points] == [1, 120_000_001, 180_000_000]
    assert len(context.state.target_tape.events) == len(context.state.prediction_refs) == 1
    assert context.state.pending_orders == {}
    events = context.state.execution_ledger.order_events
    assert [(event.ts_us, event.status) for event in events] == [(1, OrderStatus.PENDING), (120_000_001, OrderStatus.EXPIRED)]
    assert context.state.portfolio_state.positions == {'B': 100}
    assert context.state.portfolio_state.cash == 2000


def test_risk_keeps_original_target_pre_control_snapshot_and_post_settlement_outcome(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput,
) -> None:
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='6000', positions={'A': 400},
        sellable={'A': 400}, average_cost={'A': '10'}, peak_equity=10000)
    fees = FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=10)
    config = replay_step.execution.model_copy(update={'fees': fees, 'end_ts_us': 50})
    strategy = replay_step.strategy.model_copy(update={'params': replay_step.strategy.params.model_copy(
        update={'target_quantity': 1000})})
    risk = RiskConfig(rules=({'type': 'max_single_name', 'max_ratio': .2},))
    step = ReplayAccountStep(initial_account=initial, execution=config, strategy=strategy, risk=risk,
        prediction_dir=replay_step.prediction_dir, progress_clock=lambda: 0)
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A',), Path('/prediction'))
    context = step.run(_context(BacktestInputs(market=market_input, decisions={1: batch})))
    state = context.state
    observation = state.risk_observations[0]

    assert state.target_tape.events[0].targets == observation.case.target == {'A': 1000}
    assert observation.decision.adjusted == {'A': 200}
    assert observation.case.context.positions == {'A': 400}
    assert observation.case.context.equity == state.initial_equity == 10000
    assert observation.case.context.cash == 6000
    assert observation.outcome.positions == {'A': 200}
    assert observation.outcome.equity == state.equity_curve.points[0].equity == 9990
    assert observation.outcome.peak_equity == 10000 and observation.outcome.cash == 7990
    state.portfolio_state.positions.clear()
    assert observation.case.context.positions == {'A': 400}
    assert observation.outcome.positions == {'A': 200}
    assert context.metrics.applied_risk == risk
    assert 'risk' not in context.metrics.not_run


def test_frozen_commands_keep_quantities_and_original_same_time_order(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput,
) -> None:
    order = OrderIntent(10, 'A', Side.BUY, 100, 2, 10, 90, Decimal('10'))
    replacement = OrderIntent(10, 'A', Side.BUY, 100, 1, 10, 90, Decimal('10'))
    commands = OrderCommandSchedule((order, CancelOrders(10, (2,)), replacement),
        start='2026-07-03', end='2026-07-03', market=market_input, start_ts_us=1, end_ts_us=50)
    config = replay_step.execution.model_copy(update={'end_ts_us': 50,
        'fill': LimitPriceFillConfig(type='limit_price', slippage_bp=0)})
    step = ReplayAccountStep(initial_account=replay_step.initial_account, execution=config, strategy=None, risk=None,
        prediction_dir=replay_step.prediction_dir, progress_clock=lambda: 0)
    context = step.run(_context(BacktestInputs(market=market_input, decisions=commands)))

    assert context.state.execution_ledger.commands == [order, CancelOrders(10, (2,)), replacement]
    assert context.state.pending_orders[1].intent == replacement
    assert 2 not in context.state.pending_orders
    assert context.state.prediction_refs == context.state.signal_tape.events == context.state.target_tape.events == []
    assert context.metrics.not_run == {'signal': 'frozen_commands', 'strategy': 'frozen_commands', 'risk': 'no_business_risk'}


@pytest.mark.parametrize(('times', 'expected'), [([0, .1, .2, .3], []), ([0, 5, 10, 15], [1, 2])])
def test_progress_is_throttled_and_omits_final_tick(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput, times: list[float], expected: list[int],
) -> None:
    market = ReplayMarketInput.model_validate({**market_input.model_dump(), 'marks': [
        {'ts_us': ts, 'trade_date': '2026-07-03', 'prices': {'A': '10', 'B': '10'}, 'kind': 'mark'}
        for ts in (1, 2, 3)]})
    step = ReplayAccountStep(initial_account=replay_step.initial_account,
        execution=replay_step.execution.model_copy(update={'end_ts_us': 3}),
        strategy=replay_step.strategy, risk=None, prediction_dir=replay_step.prediction_dir,
        progress_clock=Mock(side_effect=times))
    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.steps.replay_account')
    try:
        step.run(_context(BacktestInputs(market=market, decisions={})))
    finally:
        logs.remove(sink)

    assert len(messages) == len(expected)
    for message, ts in zip(messages, expected, strict=True):
        assert f'ts_us={ts} processed={ts}' in message


def test_tick_failure_preserves_original_error_and_does_not_publish_completed_state(
    replay_step: ReplayAccountStep, market_input: ReplayMarketInput, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError('source unavailable')
    monkeypatch.setattr(AccountExecutionSimulator, 'advance', Mock(side_effect=error))
    context = _context(BacktestInputs(market=market_input, decisions={}))
    with pytest.raises(RuntimeError) as caught:
        replay_step.run(context)
    assert caught.value is error
    assert error.__notes__ == ['回测时点; trade_date=2026-07-03 ts_us=1']
    assert context.state is None
