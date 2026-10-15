# filepath: tests/trading/backtest/test_pipeline.py
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src import logs
from src.config.backtest_config import (
    FakeExecutionConfig, FeeConfig, InitialAccount, LimitPriceFillConfig, QuotedFillConfig,
    RiskConfig, ThresholdStrategyConfig,
)
from src.observability.instrumentation import Instrumentation
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs, TickState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.initialize_account import InitializeAccountStep
from src.trading.backtest.steps.update_market import UpdateMarketStep
from src.trading.backtest.steps.advance_orders import AdvanceOrdersStep
from src.trading.backtest.steps.value_account import ValueAccountStep
from src.trading.backtest.steps.generate_signal import GenerateSignalStep
from src.trading.backtest.steps.generate_targets import GenerateTargetsStep
from src.trading.backtest.steps.apply_risk import ApplyRiskStep
from src.trading.backtest.steps.submit_targets import SubmitTargetsStep
from src.trading.backtest.steps.submit_frozen_commands import SubmitFrozenCommandsStep
from src.trading.backtest.steps.complete_risk_observation import CompleteRiskObservationStep
from src.trading.backtest.steps.schedule_wakeups import ScheduleWakeupsStep
from src.trading.core.events import CancelOrders, OrderIntent, OrderStatus, Side, SignalEvent, TargetEvent
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
def initialize_step() -> InitializeAccountStep:
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='2000', positions={'B': 100},
        sellable={'B': 60}, average_cost={'B': '10'}, peak_equity=3000)
    config = FakeExecutionConfig(
        market_source={'frequency': 'minute', 'start': '2026-07-03', 'end': '2026-07-03',
                       'symbols': ['600000', '000001']}, end_ts_us=180_000_000,
        quantity_rules={s: QuantityRules(1, 1, 1, 1, 1, Decimal('0.01'), True) for s in ('A', 'B')},
        fees=FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=0),
        annual_factor=252, fill=QuotedFillConfig(type='quoted'),
    )
    strategy = ThresholdStrategyConfig(type='threshold', params={'threshold': .5, 'target_quantity': 100},
        order_window_minutes=2, quote=QuoteRule(Decimal(0), Decimal(0)))
    return InitializeAccountStep(initial_account=initial, execution=config, strategy=strategy, risk=None)


def _context(inputs: BacktestInputs) -> BacktestContext:
    return BacktestContext(inputs=inputs, metrics=BacktestMetrics(mode='strategy_eval', parameters={}, not_run={}))


def _run_account(
    initial: InitializeAccountStep, context: BacktestContext, *, prediction_dir: Path,
    progress_clock: Callable[[], float] = lambda: 0,
) -> BacktestContext:
    decisions = ((SubmitFrozenCommandsStep(),) if initial.strategy is None else (
        GenerateSignalStep(prediction_dir=prediction_dir),
        GenerateTargetsStep(), *((ApplyRiskStep(),) if initial.risk is not None else ()),
        SubmitTargetsStep(order_window_minutes=initial.strategy.order_window_minutes),
    ))
    return BacktestPipeline(
        startup_steps=(SimpleNamespace(run=lambda current: context), initial),
        tick_steps=(UpdateMarketStep(), AdvanceOrdersStep(), ValueAccountStep(), *decisions,
                    ValueAccountStep(), *((CompleteRiskObservationStep(),) if initial.risk is not None else ()),
                    ScheduleWakeupsStep()),
        result_steps=(), instrumentation=Instrumentation('run'), progress_clock=progress_clock,
    ).run(metrics=context.metrics)


def test_each_run_owns_account_progress_and_records_only_current_signals(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput,
    tmp_path: Path,
) -> None:
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A', 'B'), Path('/prediction'))
    inputs = BacktestInputs(market=market_input, decisions={1: batch})
    first, second = _context(inputs), _context(inputs)
    assert _run_account(initialize_step, first, prediction_dir=tmp_path / 'predictions') is first
    _run_account(initialize_step, second, prediction_dir=tmp_path / 'predictions')

    assert first.state is not second.state
    assert first.market is not second.market and first.executor is not second.executor
    assert first.clock is not second.clock and first.constructor is not second.constructor
    assert first.tick is second.tick is None
    assert first.state.portfolio_state.positions == second.state.portfolio_state.positions == {'A': 100, 'B': 100}
    assert first.state.portfolio_state.cash == second.state.portfolio_state.cash == 1000
    assert first.state.signal_tape.events == [batch.event]
    assert first.state.prediction_refs == [batch.prediction_ref]
    assert first.state.decision_dates == {1: '2026-07-03'}
    assert [event.targets for event in first.state.target_tape.events] == [{'A': 100, 'B': 100}]
    assert [point.ts_us for point in first.state.equity_curve.points] == [1, 180_000_000]
    first.state.portfolio_state.positions['B'] = 200
    first.state.decision_dates.clear()
    assert second.state.portfolio_state.positions['B'] == initialize_step.initial_account.positions['B'] == 100
    assert second.state.portfolio_state.t1_sellable('B') == 60
    assert second.state.decision_dates == {1: '2026-07-03'}
    assert second.state.equity_curve.starting_peak_equity == 3000
    assert second.metrics.applied_risk is None
    assert second.metrics.not_run == {'risk': 'no_business_risk', 'price_feasibility': 'quoted_fills',
                                       'market_liquidity': 'quoted_fills'}


def test_dynamic_order_expiry_wakes_account_without_repeating_signal_or_target(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput,
    tmp_path: Path,
) -> None:
    config = initialize_step.execution.model_copy(update={'fill': LimitPriceFillConfig(type='limit_price', slippage_bp=0)})
    step = InitializeAccountStep(initial_account=initialize_step.initial_account, execution=config,
        strategy=initialize_step.strategy, risk=None)
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A', 'B'), Path('/prediction'))
    context = _run_account(step, _context(BacktestInputs(market=market_input, decisions={1: batch})), prediction_dir=tmp_path / 'predictions')

    assert [point.ts_us for point in context.state.equity_curve.points] == [1, 120_000_001, 180_000_000]
    assert len(context.state.target_tape.events) == len(context.state.prediction_refs) == 1
    assert context.state.pending_orders == {}
    events = context.state.execution_ledger.order_events
    assert [(event.ts_us, event.status) for event in events] == [(1, OrderStatus.PENDING), (120_000_001, OrderStatus.EXPIRED)]
    assert context.state.portfolio_state.positions == {'B': 100}
    assert context.state.portfolio_state.cash == 2000


def test_risk_keeps_original_target_pre_control_snapshot_and_post_settlement_outcome(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput,
    tmp_path: Path,
) -> None:
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='6000', positions={'A': 400},
        sellable={'A': 400}, average_cost={'A': '10'}, peak_equity=10000)
    fees = FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=10)
    config = initialize_step.execution.model_copy(update={'fees': fees, 'end_ts_us': 50})
    strategy = initialize_step.strategy.model_copy(update={'params': initialize_step.strategy.params.model_copy(
        update={'target_quantity': 1000})})
    risk = RiskConfig(rules=({'type': 'max_single_name', 'max_ratio': .2},))
    step = InitializeAccountStep(initial_account=initial, execution=config, strategy=strategy, risk=risk)
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A',), Path('/prediction'))
    context = _run_account(step, _context(BacktestInputs(market=market_input, decisions={1: batch})), prediction_dir=tmp_path / 'predictions')
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
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput,
    tmp_path: Path,
) -> None:
    order = OrderIntent(10, 'A', Side.BUY, 100, 2, 10, 90, Decimal('10'))
    replacement = OrderIntent(10, 'A', Side.BUY, 100, 1, 10, 90, Decimal('10'))
    commands = OrderCommandSchedule((order, CancelOrders(10, (2,)), replacement),
        start='2026-07-03', end='2026-07-03', market=market_input, start_ts_us=1, end_ts_us=50)
    config = initialize_step.execution.model_copy(update={'end_ts_us': 50,
        'fill': LimitPriceFillConfig(type='limit_price', slippage_bp=0)})
    step = InitializeAccountStep(initial_account=initialize_step.initial_account, execution=config, strategy=None, risk=None)
    context = _run_account(step, _context(BacktestInputs(market=market_input, decisions=commands)), prediction_dir=tmp_path / 'predictions')

    assert context.state.execution_ledger.commands == [order, CancelOrders(10, (2,)), replacement]
    assert context.state.pending_orders[1].intent == replacement
    assert 2 not in context.state.pending_orders
    assert context.state.prediction_refs == context.state.signal_tape.events == context.state.target_tape.events == []
    assert context.metrics.not_run == {'signal': 'frozen_commands', 'strategy': 'frozen_commands', 'risk': 'no_business_risk'}


@pytest.mark.parametrize(('times', 'expected'), [([0, .1, .2, .3], []), ([0, 5, 10, 15], [1, 2])])
def test_progress_is_throttled_and_omits_final_tick(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput, times: list[float], expected: list[int],
    tmp_path: Path,
) -> None:
    market = ReplayMarketInput.model_validate({**market_input.model_dump(), 'marks': [
        {'ts_us': ts, 'trade_date': '2026-07-03', 'prices': {'A': '10', 'B': '10'}, 'kind': 'mark'}
        for ts in (1, 2, 3)]})
    step = InitializeAccountStep(initial_account=initialize_step.initial_account,
        execution=initialize_step.execution.model_copy(update={'end_ts_us': 3}),
        strategy=initialize_step.strategy, risk=None)
    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.pipeline')
    try:
        _run_account(step, _context(BacktestInputs(market=market, decisions={})), progress_clock=Mock(side_effect=times), prediction_dir=tmp_path / 'predictions')
    finally:
        logs.remove(sink)

    assert len(messages) == len(expected)
    for message, ts in zip(messages, expected, strict=True):
        assert f'ts_us={ts} processed={ts}' in message


def test_tick_failure_preserves_original_error_and_account_facts(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    error = RuntimeError('source unavailable')
    monkeypatch.setattr(AccountExecutionSimulator, 'advance', Mock(side_effect=error))
    context = _context(BacktestInputs(market=market_input, decisions={}))
    with pytest.raises(RuntimeError) as caught:
        _run_account(initialize_step, context, prediction_dir=tmp_path / 'predictions')
    assert caught.value is error
    assert error.__notes__ == ['Pipeline 执行; scope=run step=AdvanceOrdersStep',
                                '回测时点; trade_date=2026-07-03 ts_us=1']
    assert context.state.execution_ledger.commands == []
    assert context.state.prediction_refs == []


def test_pipeline_owns_groups_and_forwards_contexts_with_fresh_ticks_and_dynamic_wakeups(
    market_input: ReplayMarketInput,
) -> None:
    metrics = BacktestMetrics(mode='full_backtest', parameters={}, not_run={})
    next_metrics = metrics.model_copy(update={'not_run': {'signal': 'returned_context'}})
    contexts: list[BacktestContext] = []
    ticks: list[TickState] = []
    instrumentation = MagicMock(spec=Instrumentation)
    instrumentation.measure.side_effect = lambda name, operation, context: operation(context)

    def startup(context: BacktestContext) -> BacktestContext:
        contexts.append(context)
        assert context.metrics is metrics and context.inputs is None and context.state is None
        assert context.tick is context.clock is None
        return replace(context, metrics=next_metrics, inputs=BacktestInputs(market_input, {}),
                       clock=ReplayClock.from_iterable((1, 3)))

    def decide(context: BacktestContext) -> BacktestContext:
        tick = context.tick
        ticks.append(tick)
        assert context.metrics is next_metrics
        assert tick.signal is tick.original_target is tick.execution_target is tick.risk_observation is None
        assert tick.cancel_order_ids == ()
        if tick.ts_us == 1:
            context.clock.schedule(ts_us=2)
        return replace(context, tick=replace(tick, execution_target=TargetEvent(tick.ts_us, {'A': 100}),
                                             cancel_order_ids=(tick.ts_us,)))

    def consume(context: BacktestContext) -> BacktestContext:
        assert context.tick.execution_target.targets == {'A': 100}
        assert context.tick.cancel_order_ids == (context.tick.ts_us,)
        return replace(context)

    def finish(context: BacktestContext) -> BacktestContext:
        assert context.tick is None and context.metrics is next_metrics
        assert len(context.clock) == 0
        return replace(context)

    start = SimpleNamespace(run=Mock(side_effect=startup))
    decision = SimpleNamespace(run=Mock(side_effect=decide))
    consumer = SimpleNamespace(run=Mock(side_effect=consume))
    result = SimpleNamespace(run=Mock(side_effect=finish))
    startup_steps, tick_steps, result_steps = [start], [decision, consumer], [result]
    pipeline = BacktestPipeline(startup_steps=startup_steps, tick_steps=tick_steps, result_steps=result_steps,
                                instrumentation=instrumentation, progress_clock=lambda: 0)
    startup_steps.clear()
    tick_steps.clear()
    result_steps.clear()
    first = pipeline.run(metrics=metrics)
    second = pipeline.run(metrics=metrics)

    assert contexts[0] is not contexts[1]
    assert len({id(tick) for tick in ticks}) == 6
    assert [tick.ts_us for tick in ticks] == [1, 2, 3] * 2
    assert first is not result.run.call_args_list[0].args[0]
    assert first.metrics is second.metrics is next_metrics
    assert instrumentation.__enter__.call_count == instrumentation.__exit__.call_count == 2
    assert [call.args[1] for call in instrumentation.measure.call_args_list] == (
        [start.run] + [decision.run, consumer.run] * 3 + [result.run]) * 2


@pytest.mark.parametrize('group', ['startup', 'tick', 'result'])
@pytest.mark.parametrize('failed_index', [0, 1])
def test_failure_stops_remaining_steps_ticks_and_result_publication(
    market_input: ReplayMarketInput, group: str, failed_index: int,
) -> None:
    error = OSError('step unavailable')
    metrics = BacktestMetrics(mode='full_backtest', parameters={}, not_run={})
    called: list[tuple[str, int]] = []

    def step(phase: str, index: int) -> SimpleNamespace:
        def run(context: BacktestContext) -> BacktestContext:
            called.append((phase, index))
            if (phase, index) == (group, failed_index):
                raise error
            if (phase, index) == ('startup', 0):
                return replace(context, inputs=BacktestInputs(market_input, {}),
                               clock=ReplayClock.from_iterable((1, 2)))
            return context
        return SimpleNamespace(run=run)

    pipeline = BacktestPipeline(
        startup_steps=tuple(step('startup', i) for i in range(2)),
        tick_steps=tuple(step('tick', i) for i in range(2)),
        result_steps=tuple(step('result', i) for i in range(2)),
        instrumentation=Instrumentation('run'), progress_clock=lambda: 0,
    )
    with pytest.raises(OSError) as caught:
        pipeline.run(metrics=metrics)
    assert caught.value is error
    expected_notes = ['Pipeline 执行; scope=run step=SimpleNamespace']
    if group == 'tick':
        expected_notes.append('回测时点; trade_date=2026-07-03 ts_us=1')
    assert error.__notes__ == expected_notes
    expected = [('startup', 0), ('startup', 1)] + [('tick', 0), ('tick', 1)] * 2 + [('result', 0), ('result', 1)]
    assert called == expected[:expected.index((group, failed_index)) + 1]


def test_old_fills_are_valued_before_risk_and_new_fills_before_outcome(
    initialize_step: InitializeAccountStep, market_input: ReplayMarketInput,
    tmp_path: Path,
) -> None:
    decision_us = 60_000_000
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='20000', positions={},
                             sellable={}, average_cost={}, peak_equity=20000)
    config = initialize_step.execution.model_copy(update={
        'end_ts_us': decision_us + 1,
        'fees': FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=10),
    })
    strategy = initialize_step.strategy.model_copy(update={'params': initialize_step.strategy.params.model_copy(
        update={'target_quantity': 1000})})
    initializer = InitializeAccountStep(initial_account=initial, execution=config, strategy=strategy,
        risk=RiskConfig(rules=({'type': 'max_single_name', 'max_ratio': .1},)))
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(decision_us, {'A': .8}), ('A',), Path('/prediction'))
    context = _context(BacktestInputs(market_input, {decision_us: batch}))

    def seed_order(current: BacktestContext) -> BacktestContext:
        current.executor.submit_orders(orders=(
            OrderIntent(1, 'A', Side.BUY, 100, 1, decision_us, 180_000_000, Decimal('10')),
        ))
        return current

    pipeline = BacktestPipeline(
        startup_steps=(SimpleNamespace(run=lambda current: context), initializer, SimpleNamespace(run=seed_order)),
        tick_steps=(UpdateMarketStep(), AdvanceOrdersStep(), ValueAccountStep(),
                    GenerateSignalStep(prediction_dir=tmp_path / 'predictions'),
                    GenerateTargetsStep(), ApplyRiskStep(), SubmitTargetsStep(order_window_minutes=2),
                    ValueAccountStep(), CompleteRiskObservationStep(), ScheduleWakeupsStep()),
        result_steps=(), instrumentation=Instrumentation('run'), progress_clock=lambda: 0,
    )
    state = pipeline.run(metrics=context.metrics).state
    observation = state.risk_observations[1]
    assert observation.case.target == state.target_tape.events[0].targets == {'A': 1000}
    assert observation.case.context.positions == {'A': 100}
    assert observation.case.context.cash == 18990 and observation.case.context.equity == 19990
    assert observation.case.context.pending_orders == ()
    assert observation.decision.adjusted == {'A': 199}
    assert observation.outcome.positions == {'A': 199}
    assert observation.outcome.cash == 17990 and observation.outcome.equity == 19980
    assert observation.outcome.peak_equity == state.initial_equity == 20000
    assert [(point.ts_us, point.equity) for point in state.equity_curve.points] == [
        (1, 20000), (decision_us, 19980), (decision_us + 1, 19980),
    ]
    assert [order.qty for order in state.execution_ledger.orders_by_id.values()] == [100, 99]
    assert len(state.risk_observations) == 3
    assert state.risk_observations[-1].case.target is None
    assert state.risk_observations[-1].decision.adjusted is None
    assert len(state.signal_tape.events) == len(state.target_tape.events) == len(state.prediction_refs) == 1
    state.portfolio_state.positions.clear()
    assert observation.case.context.positions == {'A': 100}
    assert observation.outcome.positions == {'A': 199}
