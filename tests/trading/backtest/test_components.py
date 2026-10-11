# filepath: tests/trading/backtest/test_components.py
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.access import Access
from src.config.backtest_config import FakeExecutionConfig, FeeConfig, InitialAccount, LimitPriceFillConfig
from src.trading.backtest.components import prepare_market_input
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.execution.policies.constraints import QuantityRules


@pytest.fixture
def market_scenario(tmp_path: Path) -> tuple[FakeExecutionConfig, InitialAccount, Mock]:
    market_ref = tmp_path / 'market.json'
    market_ref.write_text(json.dumps({
        'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'close_ts_us': 180_000_000,
                      'windows': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'end_ts_us': 180_000_000}]}],
        'marks': [{'ts_us': 1, 'trade_date': '2026-07-03', 'prices': {'A': '10', 'B': '20'}, 'kind': 'mark'}],
        'observations': [{'observation_id': 'bar', 'symbol': 'A', 'price': '11', 'volume_qty': 100,
                          'time': {'unit': 'minute', 'size': 1, 'trade_date': '2026-07-03', 'start_ts_us': 0,
                                   'end_ts_us': 60_000_000, 'available_ts_us': 60_000_000}}],
    }))
    config = FakeExecutionConfig(
        market_ref=market_ref, end_ts_us=120_000_000,
        quantity_rules={symbol: QuantityRules(1, 1, 1, 1, 1, Decimal('0.01'), True) for symbol in ('A', 'B')},
        fees=FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=0),
        annual_factor=252, fill=LimitPriceFillConfig(type='limit_price', slippage_bp=0),
        risk_check_ts_us=(1, 120_000_000),
    )
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash=1000,
        positions={'B': 100}, sellable={'B': 100}, average_cost={'B': 20}, peak_equity=3000)
    access = Mock(spec=Access)
    access.trade_dates.return_value = ['2026-07-03']
    return config, initial, access


def test_prepare_market_input_returns_facts_and_accepts_risk_checks_at_both_simulation_endpoints(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
) -> None:
    config, initial, access = market_scenario
    source = prepare_market_input(config=config, initial=initial, access=access)

    assert isinstance(source, ReplayMarketInput)
    assert json.loads(source.model_dump_json()) == json.loads(config.market_ref.read_text())
    assert source.timestamps() == (0, 1, 60_000_000, 180_000_000)
    assert source.session_at(initial.ts_us).trade_date == initial.trade_date
    assert source.session_at(config.end_ts_us).trade_date == '2026-07-03'
    access.trade_dates.assert_called_once_with(start_date='2026-07-03', end_date='2026-07-03')


def test_prepare_market_input_rejects_missing_formal_trading_days(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
) -> None:
    config, initial, access = market_scenario
    payload = json.loads(config.market_ref.read_text())
    payload['sessions'].append({'trade_date': '2026-07-07', 'start_ts_us': 360_000_000,
        'close_ts_us': 540_000_000,
        'windows': [{'trade_date': '2026-07-07', 'start_ts_us': 360_000_000, 'end_ts_us': 540_000_000}]})
    config.market_ref.write_text(json.dumps(payload))
    access.trade_dates.return_value = ['2026-07-03', '2026-07-06', '2026-07-07']

    with pytest.raises(ValueError, match='exactly cover the formal trading calendar'):
        prepare_market_input(config=config, initial=initial, access=access)
    access.trade_dates.assert_called_once_with(start_date='2026-07-03', end_date='2026-07-07')


@pytest.mark.parametrize(('start_ts_us', 'trade_date', 'end_ts_us', 'error'), [
    (-1, '2026-07-03', 120_000_000, 'outside the prepared account sessions'),
    (1, '2026-07-06', 120_000_000, 'initial snapshot trade_date disagrees'),
    (1, '2026-07-03', 180_000_001, 'outside the prepared account sessions'),
])
def test_prepare_market_input_rejects_account_and_end_outside_coverage(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
    start_ts_us: int, trade_date: str, end_ts_us: int, error: str,
) -> None:
    config, initial, access = market_scenario
    initial = initial.model_copy(update={'ts_us': start_ts_us, 'trade_date': trade_date})
    config = config.model_copy(update={'end_ts_us': end_ts_us})
    with pytest.raises(ValueError, match=error):
        prepare_market_input(config=config, initial=initial, access=access)


@pytest.mark.parametrize(('ts_us', 'error'), [
    (0, 'risk check is outside the simulation interval'),
    (120_000_001, 'risk check is outside the simulation interval'),
    (180_000_001, 'outside the prepared account sessions'),
])
def test_prepare_market_input_rejects_risk_checks_outside_coverage(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock], ts_us: int, error: str,
) -> None:
    config, initial, access = market_scenario
    config = config.model_copy(update={'risk_check_ts_us': (ts_us,)})
    with pytest.raises(ValueError, match=error):
        prepare_market_input(config=config, initial=initial, access=access)


@pytest.mark.parametrize('symbol', ['A', 'B'])
def test_prepare_market_input_requires_rules_for_observed_and_held_symbols(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock], symbol: str,
) -> None:
    config, initial, access = market_scenario
    config = config.model_copy(update={'quantity_rules': {
        key: rule for key, rule in config.quantity_rules.items() if key != symbol}})
    with pytest.raises(ValueError, match='quantity rules must cover all observed and held symbols'):
        prepare_market_input(config=config, initial=initial, access=access)


@pytest.mark.parametrize('fill', ['limit_price', 'reference', 'quoted'])
def test_prepare_market_input_requires_volume_only_for_price_model_execution(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock], fill: str,
) -> None:
    config, initial, access = market_scenario
    payload = json.loads(config.market_ref.read_text())
    payload['observations'][0]['volume_qty'] = None
    config.market_ref.write_text(json.dumps(payload))
    if fill == 'limit_price':
        with pytest.raises(ValueError, match='requires explicit observed volume_qty'):
            prepare_market_input(config=config, initial=initial, access=access)
    else:
        config = FakeExecutionConfig.model_validate({**config.model_dump(), 'fill': {'type': fill}})
        source = prepare_market_input(config=config, initial=initial, access=access)
        assert source.observations[0].volume_qty is None


@pytest.mark.parametrize('payload', ['{', '{"price_basis": "raw", "quantity_unit": "shares", "sessions": []}'])
def test_prepare_market_input_rejects_invalid_files_before_reading_calendar(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock], payload: str,
) -> None:
    config, initial, access = market_scenario
    config.market_ref.write_text(payload)
    with pytest.raises(ValidationError):
        prepare_market_input(config=config, initial=initial, access=access)
    access.trade_dates.assert_not_called()


def test_prepare_market_input_preserves_file_read_failure(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
) -> None:
    config, initial, access = market_scenario
    config.market_ref.unlink()
    with pytest.raises(FileNotFoundError):
        prepare_market_input(config=config, initial=initial, access=access)
    access.trade_dates.assert_not_called()


def test_prepare_market_input_does_not_coerce_timestamp_strings(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
) -> None:
    config, initial, access = market_scenario
    payload = json.loads(config.market_ref.read_text())
    payload['sessions'][0]['start_ts_us'] = '0'
    config.market_ref.write_text(json.dumps(payload))
    with pytest.raises(ValidationError, match='start_ts_us'):
        prepare_market_input(config=config, initial=initial, access=access)
    access.trade_dates.assert_not_called()


def test_prepare_market_input_preserves_calendar_failure(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock],
) -> None:
    config, initial, access = market_scenario
    error = RuntimeError('calendar unavailable')
    access.trade_dates.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        prepare_market_input(config=config, initial=initial, access=access)
    assert caught.value is error
