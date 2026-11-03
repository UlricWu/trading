# filepath: tests/trading/backtest/steps/test_prepare_initial_marks.py
from datetime import date, time
from unittest.mock import Mock, call

import pandas as pd
import pytest

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.trading.backtest.context import BacktestContext, BacktestInputs
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_initial_marks import PrepareInitialMarksStep
from src.utils.datetime_utils import DateTimeUtils


@pytest.fixture
def initial_context() -> tuple[InitialAccount, BacktestContext]:
    start, open_us, close = (DateTimeUtils.local_time_to_utc_epoch_us(clock, date(2026, 7, 20))
                             for clock in (time(0), time(9, 30), time(15)))
    source = ReplayMarketInput.model_validate({
        "price_basis": "raw", "quantity_unit": "shares", "observations": [],
        "sessions": [{"trade_date": "2026-07-20", "start_ts_us": start, "close_ts_us": close,
                      "windows": [{"trade_date": "2026-07-20", "start_ts_us": open_us, "end_ts_us": close}]}],
        "marks": [{"ts_us": close, "trade_date": "2026-07-20", "prices": {"600000": "99"}, "kind": "daily_close"}],
    })
    initial = InitialAccount(ts_us=open_us, trade_date="2026-07-20", cash=1000,
        positions={"600000": 100}, sellable={"600000": 100}, average_cost={"600000": 7}, peak_equity=2000)
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
                              inputs=BacktestInputs(source, {}))
    return initial, context


def test_initial_holdings_use_last_prior_close_across_multiple_suspended_sessions(
    initial_context: tuple[InitialAccount, BacktestContext],
) -> None:
    initial, context = initial_context
    access = Mock(spec=Access)
    access.recent_trade_dates.side_effect = [["2026-07-17", "2026-07-20"], ["2026-07-16", "2026-07-17"]]
    access.daily_bars.side_effect = [pd.DataFrame({"symbol": [], "close": []}),
                                    pd.DataFrame({"symbol": ["600000"], "close": [10.0]})]
    PrepareInitialMarksStep(access, initial).run(context)
    access.daily_bars.assert_has_calls([call(trade_date="2026-07-17"), call(trade_date="2026-07-16")])
    assert len(context.initial_marks) == 1
    mark = context.initial_marks[0]
    assert mark.prices["600000"] == 10
    assert mark.trade_date == "2026-07-16"
    assert mark.ts_us < initial.ts_us
    market = ReplayMarket(context.inputs.market, initial_marks=context.initial_marks)
    market.on_time(initial.ts_us)
    assert market.get_price("600000") == 10
    assert market.observations_at(initial.ts_us) == ()
    market.on_time(context.inputs.market.sessions[0].close_ts_us)
    assert market.get_price("600000") == 99


def test_initial_history_failure_does_not_use_cost_or_future_close(
    initial_context: tuple[InitialAccount, BacktestContext],
) -> None:
    initial, context = initial_context
    access = Mock(spec=Access)
    access.recent_trade_dates.return_value = ["2026-07-17", "2026-07-20"]
    access.daily_bars.side_effect = FileNotFoundError("required Meta: daily_bar/2026-07-17")
    with pytest.raises(FileNotFoundError, match="required Meta"):
        PrepareInitialMarksStep(access, initial).run(context)
    assert context.initial_marks == ()


def test_existing_visible_price_requires_no_history_read(
    initial_context: tuple[InitialAccount, BacktestContext],
) -> None:
    initial, context = initial_context
    initial = initial.model_copy(update={"ts_us": context.inputs.market.sessions[0].close_ts_us})
    access = Mock(spec=Access)
    PrepareInitialMarksStep(access, initial).run(context)
    assert context.initial_marks == ()
    access.daily_bars.assert_not_called()
    access.recent_trade_dates.assert_not_called()
