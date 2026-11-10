# filepath: tests/trading/backtest/test_risk_effectiveness.py
from dataclasses import replace
from decimal import Decimal

import pytest

from src.config.backtest_config import RiskConfig
from src.trading.backtest.risk_effectiveness import evaluate_risk_effectiveness
from src.trading.core.events import OrderEvent, OrderIntent, OrderStatus, PendingOrder, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.risk.base import RiskContext, RiskDecision
from src.trading.risk.evaluation import RiskCase, RiskObservation


def _observation(before: RiskContext, after: RiskContext) -> RiskObservation:
    return RiskObservation(RiskCase(str(before.ts_us), '2026-07-03', None, before),
        RiskDecision(None, (), 'no_action', ()), after)


@pytest.mark.parametrize('rule', [
    {'type': 'max_single_name', 'max_ratio': 0.2},
    {'type': 'max_gross_exposure', 'max_gross_ratio': 0.2},
])
def test_pending_sells_do_not_reduce_exposure_and_only_remaining_buys_add_risk(rule: dict[str, object]) -> None:
    buy = OrderIntent(0, 'A', Side.BUY, 100, 1, 0, 100, Decimal('10'))
    sell = OrderIntent(0, 'A', Side.SELL, 300, 2, 0, 100, Decimal('10'))
    before = RiskContext(1, {'A': 10}, 10000, 6000, 10000, {'A': 400}, (
        PendingOrder(buy, 50, Decimal('500'), 0, Decimal('10')),
        PendingOrder(sell, 300, Decimal('0'), 300, Decimal('10')),
    ), {'A': 1})
    partial = replace(before, positions={'A': 250}, cash=7500, pending_orders=(
        PendingOrder(sell, 50, Decimal(0), 50, Decimal('10')),
    ))
    complete = replace(before, ts_us=11, positions={'A': 200}, cash=8000, pending_orders=())
    result = evaluate_risk_effectiveness((_observation(before, partial), _observation(complete, complete)),
        config=RiskConfig(rules=(rule,)), ledger=ExecutionLedger()).rules[0]
    first = result.observations[0]
    assert first.before.value == 0.45
    assert first.before.held_notional == 4000 and first.before.pending_buy_notional == 500
    assert first.before.required_reduction_notional == 2500
    assert first.after.value == 0.25 and first.after.required_reduction_notional == 500
    assert result.observed_breach_count == result.residual_breach_count == 1
    assert result.max_required_reduction_notional == 2500
    assert result.max_residual_reduction_notional == 500
    assert result.final_residual_reduction_notional == 0
    assert result.sampled_breach_duration_us == 10


def test_single_name_and_gross_objectives_remain_distinct_and_preserve_duplicate_rules() -> None:
    context = RiskContext(1, {'A': 10, 'B': 10}, 10000, 1000, 10000, {'A': 500, 'B': 400}, (), {})
    rules = ({'type': 'max_single_name', 'max_ratio': 0.2},
             {'type': 'max_single_name', 'max_ratio': 0.3},
             {'type': 'max_gross_exposure', 'max_gross_ratio': 0.6})
    result = evaluate_risk_effectiveness((_observation(context, context),),
        config=RiskConfig(rules=rules), ledger=ExecutionLedger())
    assert [row.final_residual_reduction_notional for row in result.rules] == [5000, 3000, 3000]
    assert [row.observations[0].after.value for row in result.rules] == [0.5, 0.5, 0.9]
    assert [row.rule.model_dump() for row in result.rules] == list(rules)
    assert all(row.sampled_breach_duration_us == 0 for row in result.rules)


@pytest.mark.parametrize('remaining', [0, 300])
def test_drawdown_measures_remaining_liquidation_not_persistent_historical_loss(remaining: int) -> None:
    before = RiskContext(1, {'A': 10}, 8000, 3000, 10000, {'A': 500}, (), {})
    after = replace(before, positions={'A': remaining}, cash=8000 - remaining * 10)
    result = evaluate_risk_effectiveness((_observation(before, after),),
        config=RiskConfig(rules=({'type': 'max_drawdown', 'max_drawdown': 0.1},)),
        ledger=ExecutionLedger()).rules[0]
    assert result.observations[0].before.value == result.observations[0].after.value == 0.2
    assert result.max_required_reduction_notional == 5000
    assert result.final_residual_reduction_notional == remaining * 10
    assert result.residual_breach_count == int(remaining > 0)


@pytest.mark.parametrize(('before_equity', 'after_equity'), [(9000, 9500), (9500, 8999)])
def test_drawdown_response_remains_due_when_pre_control_or_post_fee_threshold_is_reached(
    before_equity: float, after_equity: float,
) -> None:
    before = RiskContext(1, {'A': 10}, before_equity, before_equity - 5000, 10000, {'A': 500}, (), {})
    after = replace(before, equity=after_equity, cash=after_equity - 5000)
    result = evaluate_risk_effectiveness((_observation(before, after),),
        config=RiskConfig(rules=({'type': 'max_drawdown', 'max_drawdown': 0.1},)),
        ledger=ExecutionLedger()).rules[0]
    assert result.final_residual_reduction_notional == 5000


def test_exact_decimal_cap_does_not_report_floating_point_breach() -> None:
    context = RiskContext(1, {'A': 0.1}, 1, 0.7, 1, {'A': 3}, (), {})
    result = evaluate_risk_effectiveness((_observation(context, context),),
        config=RiskConfig(rules=({'type': 'max_single_name', 'max_ratio': 0.3},)),
        ledger=ExecutionLedger()).rules[0]
    assert result.observations[0].after.value == 0.3
    assert result.final_residual_reduction_notional == result.residual_breach_count == 0


@pytest.mark.parametrize(('rule', 'reason'), [
    ({'type': 'max_single_name', 'max_ratio': 0.2}, 'nonpositive_equity'),
    ({'type': 'max_gross_exposure', 'max_gross_ratio': 0.8}, 'nonpositive_equity'),
    ({'type': 'max_drawdown', 'max_drawdown': 0.1}, 'nonpositive_peak_equity'),
])
def test_empty_and_undefined_samples_are_not_successful_zero_risk(rule: dict[str, object], reason: str) -> None:
    config = RiskConfig(rules=(rule,))
    empty = evaluate_risk_effectiveness((), config=config, ledger=ExecutionLedger()).rules[0]
    assert empty.sample_count == empty.defined_count == 0
    assert empty.final_residual_reduction_notional is None and empty.sampled_breach_duration_us is None
    context = RiskContext(1, {}, 0, 0, 0, {}, (), {})
    result = evaluate_risk_effectiveness((_observation(context, context),),
        config=config, ledger=ExecutionLedger()).rules[0]
    assert result.sample_count == 1 and result.defined_count == 0
    assert result.max_required_reduction_notional is result.max_residual_reduction_notional is None
    assert result.final_residual_reduction_notional is None
    assert result.undefined_reason_counts == {reason: 1}
    assert 'passed' not in result.model_dump()


def test_later_facts_do_not_rewrite_prior_measurements_or_fill_undefined_duration() -> None:
    before = RiskContext(1, {'A': 10}, 10000, 5000, 10000, {'A': 500}, (), {})
    unknown = RiskContext(11, {}, 0, 0, 10000, {}, (), {})
    final = replace(before, ts_us=21)
    config = RiskConfig(rules=({'type': 'max_single_name', 'max_ratio': 0.2},))
    ledger = ExecutionLedger(order_events=[
        OrderEvent(1, 1, OrderStatus.PENDING, 100, 'accepted', None, None),
        OrderEvent(11, 1, OrderStatus.PENDING, 100, 'not_marketable', 'm1', Decimal(10)),
        OrderEvent(21, 1, OrderStatus.PENDING, 100, 'not_marketable', 'm2', Decimal(10)),
    ])
    first = _observation(before, before)
    original = evaluate_risk_effectiveness((first,), config=config, ledger=ExecutionLedger())
    extended = evaluate_risk_effectiveness((first, _observation(unknown, unknown), _observation(final, final)),
        config=config, ledger=ledger)
    assert extended.rules[0].observations[0] == original.rules[0].observations[0]
    assert extended.rules[0].sampled_breach_duration_us == 0
    assert extended.rules[0].undefined_reason_counts == {'nonpositive_equity': 1}
    assert extended.execution_reason_counts == {'accepted': 1, 'not_marketable': 2}
