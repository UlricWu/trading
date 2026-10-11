# filepath: tests/trading/backtest/test_commands.py
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.core.events import OrderIntent, CancelOrders, Side


def test_schedule_preserves_same_time_order_and_queries_do_not_consume_commands(order_market_input: ReplayMarketInput) -> None:
    order = OrderIntent(10, 'A', Side.BUY, 100, 2, 10, 90, Decimal('10'))
    cancel = CancelOrders(10, (2,))
    replacement = OrderIntent(10, 'A', Side.BUY, 100, 1, 10, 90, Decimal('10'))
    later = CancelOrders(50, (1,))
    commands = OrderCommandSchedule((order, cancel, replacement, later),
        start='2026-07-03', end='2026-07-03', market=order_market_input, start_ts_us=10, end_ts_us=50)

    assert commands.timestamps() == (10, 50)
    assert commands.at(50) == (later,)
    assert commands.at(10) == (order, cancel, replacement)
    assert commands.at(9) == commands.at(20) == commands.at(100) == ()
    assert commands.at(50) == (later,)
    assert commands.at(10) == (order, cancel, replacement)
    assert commands.timestamps() == (10, 50)


def test_schedule_owns_its_index_independently_of_the_input_container(order_market_input: ReplayMarketInput) -> None:
    first, second = CancelOrders(10, (1,)), CancelOrders(20, (2,))
    source = [first, second]
    commands = OrderCommandSchedule(source, start='2026-07-03', end='2026-07-03',
        market=order_market_input, start_ts_us=10, end_ts_us=50)
    times = commands.timestamps()
    batch = commands.at(10)

    source.reverse()
    source[0] = CancelOrders(5, (3,))
    source.append(CancelOrders(30, (4,)))

    assert commands.timestamps() == times == (10, 20)
    assert commands.at(10) == batch == (first,)
    assert commands.at(20) == (second,)
    assert commands.at(5) == commands.at(30) == ()
    with pytest.raises(TypeError):
        times[0] = 5
    with pytest.raises(TypeError):
        batch[0] = second
    assert commands.timestamps() == (10, 20)
    assert commands.at(10) == (first,)


def test_empty_schedule_has_no_times_or_commands(order_market_input: ReplayMarketInput) -> None:
    commands = OrderCommandSchedule((), start='2026-07-03', end='2026-07-03',
        market=order_market_input, start_ts_us=10, end_ts_us=50)

    assert commands.timestamps() == ()
    assert commands.at(10) == ()


@pytest.mark.parametrize('times', [(20, 10), (10, 20, 10), (201, 10)])
def test_schedule_rejects_time_regressions_before_coverage_checks(
    order_market_input: ReplayMarketInput, times: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match='commands must retain chronological order'):
        OrderCommandSchedule(tuple(CancelOrders(ts_us, (1,)) for ts_us in times),
            start='2026-07-03', end='2026-07-03', market=order_market_input, start_ts_us=10, end_ts_us=50)


@pytest.mark.parametrize(('ts_us', 'error'), [
    (9, 'outside the simulation interval'), (51, 'outside the simulation interval'),
    (201, 'outside the selected request range'), (101, 'outside the prepared account sessions'),
])
def test_schedule_rejects_dates_and_times_outside_the_run_at_construction(
    order_market_input: ReplayMarketInput, ts_us: int, error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        OrderCommandSchedule((CancelOrders(ts_us, (1,)),), start='2026-07-03', end='2026-07-03',
            market=order_market_input, start_ts_us=10, end_ts_us=50)


def test_schedule_accepts_both_request_dates_and_simulation_endpoints(order_market_input: ReplayMarketInput) -> None:
    first, last = CancelOrders(0, (1,)), CancelOrders(300, (2,))

    commands = OrderCommandSchedule((first, last), start='2026-07-03', end='2026-07-06',
        market=order_market_input, start_ts_us=0, end_ts_us=300)

    assert commands.timestamps() == (0, 300)
    assert commands.at(0) == (first,)
    assert commands.at(300) == (last,)


def test_schedule_from_files_preserves_file_and_same_time_order(tmp_path: Path, order_market_input: ReplayMarketInput) -> None:
    order = {'ts_us': 10, 'symbol': 'A', 'side': 'BUY', 'qty': 100, 'order_id': 2,
             'active_ts_us': 10, 'expires_ts_us': 90, 'limit_price': '10'}
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_text(json.dumps([order, {'ts_us': 10, 'order_ids': [2]}]))
    second.write_text(json.dumps([{**order, 'order_id': 1}, {'ts_us': 50, 'order_ids': [1]}]))

    commands = OrderCommandSchedule.from_files((first, second), start='2026-07-03', end='2026-07-03',
        market=order_market_input, start_ts_us=10, end_ts_us=50)

    assert commands.timestamps() == (10, 50)
    assert commands.at(10) == (
        OrderIntent(10, 'A', Side.BUY, 100, 2, 10, 90, Decimal('10')),
        CancelOrders(10, (2,)), OrderIntent(10, 'A', Side.BUY, 100, 1, 10, 90, Decimal('10')))
    assert commands.at(50) == (CancelOrders(50, (1,)),)


@pytest.mark.parametrize('with_file', [False, True])
def test_schedule_from_files_accepts_empty_inputs(tmp_path: Path, order_market_input: ReplayMarketInput, with_file: bool) -> None:
    ref = tmp_path / 'empty.json'
    ref.write_text('[]')

    commands = OrderCommandSchedule.from_files((ref,) if with_file else (), start='2026-07-03', end='2026-07-03',
        market=order_market_input, start_ts_us=10, end_ts_us=50)

    assert commands.timestamps() == ()
    assert commands.at(10) == ()


@pytest.mark.parametrize('across_files', [False, True])
def test_schedule_from_files_rejects_regressing_timestamps(
    tmp_path: Path, order_market_input: ReplayMarketInput, across_files: bool,
) -> None:
    payload = [{'ts_us': 50, 'order_ids': [1]}, {'ts_us': 10, 'order_ids': [1]}]
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_text(json.dumps(payload[:1] if across_files else payload))
    second.write_text(json.dumps(payload[1:] if across_files else []))

    with pytest.raises(ValueError, match='commands must retain chronological order'):
        OrderCommandSchedule.from_files((first, second), start='2026-07-03', end='2026-07-03',
            market=order_market_input, start_ts_us=10, end_ts_us=50)


@pytest.mark.parametrize(('ts_us', 'error'), [
    (9, 'outside the simulation interval'), (51, 'outside the simulation interval'),
    (201, 'outside the selected request range'), (101, 'outside the prepared account sessions'),
])
def test_schedule_from_files_rejects_dates_and_times_outside_the_run(
    tmp_path: Path, order_market_input: ReplayMarketInput, ts_us: int, error: str,
) -> None:
    ref = tmp_path / 'orders.json'
    ref.write_text(json.dumps([{'ts_us': ts_us, 'order_ids': [1]}]))

    with pytest.raises(ValueError, match=error):
        OrderCommandSchedule.from_files((ref,), start='2026-07-03', end='2026-07-03',
            market=order_market_input, start_ts_us=10, end_ts_us=50)


@pytest.mark.parametrize('payload', [
    '[', '[{"ts_us":"10","order_ids":[1]}]', '[{"ts_us":10,"order_ids":[1],"extra":true}]',
])
def test_schedule_from_files_rejects_invalid_file_contents(
    tmp_path: Path, order_market_input: ReplayMarketInput, payload: str,
) -> None:
    ref = tmp_path / 'orders.json'
    ref.write_text(payload)

    with pytest.raises(ValidationError):
        OrderCommandSchedule.from_files((ref,), start='2026-07-03', end='2026-07-03',
            market=order_market_input, start_ts_us=10, end_ts_us=50)


def test_schedule_from_files_preserves_file_read_failure(tmp_path: Path, order_market_input: ReplayMarketInput) -> None:
    ref = tmp_path / 'missing.json'

    with pytest.raises(FileNotFoundError) as caught:
        OrderCommandSchedule.from_files((ref,), start='2026-07-03', end='2026-07-03',
            market=order_market_input, start_ts_us=10, end_ts_us=50)

    assert caught.value.filename == str(ref)
