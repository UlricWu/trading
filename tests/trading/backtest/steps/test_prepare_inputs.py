# filepath: tests/trading/backtest/steps/test_prepare_inputs.py
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.access import Access
from src.config.backtest_config import FakeExecutionConfig, FeeConfig, InitialAccount, LimitPriceFillConfig, QuotedFillConfig
from src.jobs.requests import RiskRuleEvalSubmission
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.steps.prepare_inputs import PrepareInputsStep
from src.trading.backtest.market.replay import MarketDataIssue, MarkObservation, ReplayMarketInput
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.orders.constraints import QuantityRules
from src.utils.path import PathManager


@pytest.fixture
def market_scenario() -> tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput]:
    market = ReplayMarketInput.model_validate({
        'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'close_ts_us': 180_000_000,
                      'windows': [{'trade_date': '2026-07-03', 'start_ts_us': 0, 'end_ts_us': 180_000_000}]}],
        'marks': [{'ts_us': 1, 'trade_date': '2026-07-03', 'prices': {'600000': '10', '000001': '20'}, 'kind': 'mark'}],
        'observations': [{'observation_id': 'bar', 'symbol': '600000', 'price': '11', 'volume_qty': 100,
                          'time': {'unit': 'minute', 'size': 1, 'trade_date': '2026-07-03', 'start_ts_us': 0,
                                   'end_ts_us': 60_000_000, 'available_ts_us': 60_000_000}}],
    })
    config = FakeExecutionConfig(
        market_source={'frequency': 'minute', 'start': '2026-07-03', 'end': '2026-07-03',
                       'symbols': ['600000', '000001']}, end_ts_us=120_000_000,
        quantity_rules={symbol: QuantityRules(1, 1, 1, 1, 1, Decimal('0.01'), True) for symbol in ('600000', '000001')},
        fees=FeeConfig(commission_rate=0, stamp_tax_rate=0, transfer_rate=0, min_commission=0),
        annual_factor=252, fill=LimitPriceFillConfig(type='limit_price', slippage_bp=0),
        risk_check_ts_us=(1, 120_000_000),
    )
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash=1000,
        positions={'000001': 100}, sellable={'000001': 100}, average_cost={'000001': 20}, peak_equity=3000)
    access = Mock(spec=Access)
    access.trade_dates.return_value = ['2026-07-03']
    return config, initial, access, market


def _prepare_market(
    *, config: FakeExecutionConfig, initial: InitialAccount, access: Access,
    market: ReplayMarketInput, tmp_path: Path,
) -> ReplayMarketInput:
    submission = RiskRuleEvalSubmission(
        mode="risk_rule_eval", start="2026-07-03", end="2026-07-03",
        signal={"type": "recorded", "prediction_refs": ()}, min_listing_calendar_days=0,
        strategy={"type": "threshold", "params": {"threshold": 0.5, "target_quantity": 100},
                  "order_window_minutes": 2, "quote": {"buy_offset_bp": 0, "sell_offset_bp": 0}},
        initial_account=initial, execution=config,
        risk={"rules": [{"type": "max_single_name", "max_ratio": 0.2}]},
    )
    context = BacktestContext(metrics=BacktestMetrics(mode=submission.mode, parameters={}, not_run={}), market_input=market)
    returned = PrepareInputsStep(submission=submission, access=access, pm=PathManager(tmp_path)).run(context)
    assert returned is context
    assert context.state is None
    assert context.inputs.decisions == {}
    return context.inputs.market


def test_prepare_inputs_returns_facts_and_accepts_risk_checks_at_both_simulation_endpoints(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput], tmp_path: Path,
) -> None:
    config, initial, access, market = market_scenario
    source = _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)

    assert isinstance(source, ReplayMarketInput)
    assert source is market
    assert source.timestamps() == (0, 1, 60_000_000, 180_000_000)
    assert source.session_at(initial.ts_us).trade_date == initial.trade_date
    assert source.session_at(config.end_ts_us).trade_date == '2026-07-03'
    access.trade_dates.assert_not_called()


@pytest.mark.parametrize("end_ts_us", [120_000_000, 180_000_000])
def test_quote_execution_cannot_bypass_a_gap_within_the_replay_horizon(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput],
    tmp_path: Path, end_ts_us: int,
) -> None:
    config, initial, access, source = market_scenario
    config = config.model_copy(update={"fill": QuotedFillConfig(type="quoted"), "end_ts_us": end_ts_us})
    issue = MarketDataIssue(ts_us=180_000_000, trade_date="2026-07-03",
                            symbol="600000", reason="missing_daily_close")
    close = MarkObservation(ts_us=issue.ts_us, trade_date=issue.trade_date, prices={},
                            kind="daily_close", data_issues=(issue,))
    source = source.model_copy(update={"marks": (*source.marks, close)})
    if end_ts_us == 180_000_000:
        with pytest.raises(ValueError, match="requires limit_price"):
            _prepare_market(config=config, initial=initial, access=access, market=source, tmp_path=tmp_path)
    else:
        assert _prepare_market(config=config, initial=initial, access=access, market=source, tmp_path=tmp_path) is source


@pytest.mark.parametrize(('start_ts_us', 'trade_date', 'end_ts_us', 'error'), [
    (-1, '2026-07-03', 120_000_000, 'outside the prepared account sessions'),
    (1, '2026-07-06', 120_000_000, 'initial snapshot trade_date disagrees'),
    (1, '2026-07-03', 180_000_001, 'outside the prepared account sessions'),
])
def test_prepare_inputs_rejects_account_and_end_outside_coverage(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput], tmp_path: Path,
    start_ts_us: int, trade_date: str, end_ts_us: int, error: str,
) -> None:
    config, initial, access, market = market_scenario
    initial = initial.model_copy(update={'ts_us': start_ts_us, 'trade_date': trade_date})
    config = config.model_copy(update={'end_ts_us': end_ts_us})
    with pytest.raises(ValueError, match=error):
        _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)


@pytest.mark.parametrize(('ts_us', 'error'), [
    (0, 'risk check is outside the simulation interval'),
    (120_000_001, 'risk check is outside the simulation interval'),
    (180_000_001, 'outside the prepared account sessions'),
])
def test_prepare_inputs_rejects_risk_checks_outside_coverage(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput], tmp_path: Path, ts_us: int, error: str,
) -> None:
    config, initial, access, market = market_scenario
    config = config.model_copy(update={'risk_check_ts_us': (ts_us,)})
    with pytest.raises(ValueError, match=error):
        _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)


@pytest.mark.parametrize('symbol', ['600000', '000001'])
def test_prepare_inputs_requires_rules_for_observed_and_held_symbols(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput], tmp_path: Path, symbol: str,
) -> None:
    config, initial, access, market = market_scenario
    config = config.model_copy(update={'quantity_rules': {
        key: rule for key, rule in config.quantity_rules.items() if key != symbol}})
    with pytest.raises(ValueError, match='quantity rules must cover all observed and held symbols'):
        _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)


@pytest.mark.parametrize('fill', ['limit_price', 'quoted'])
def test_prepare_inputs_requires_volume_only_for_price_model_execution(
    market_scenario: tuple[FakeExecutionConfig, InitialAccount, Mock, ReplayMarketInput], tmp_path: Path, fill: str,
) -> None:
    config, initial, access, market = market_scenario
    market = market.model_copy(update={'observations': (replace(market.observations[0], volume_qty=None),)})
    if fill == 'limit_price':
        with pytest.raises(ValueError, match='requires explicit observed volume_qty'):
            _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)
    else:
        config = FakeExecutionConfig.model_validate({**config.model_dump(), 'fill': {'type': fill}})
        source = _prepare_market(config=config, initial=initial, access=access, market=market, tmp_path=tmp_path)
        assert source.observations[0].volume_qty is None
