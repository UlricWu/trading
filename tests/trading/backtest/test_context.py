# filepath: tests/trading/backtest/test_context.py
from pathlib import Path

from src.trading.backtest.context import BacktestInputs
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.core.events import SignalEvent


def test_prepared_inputs_own_signal_index(order_market_input: ReplayMarketInput) -> None:
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A',), Path('/prediction'))
    decisions = {1: batch}
    inputs = BacktestInputs(market=order_market_input, decisions=decisions)
    decisions.clear()
    assert inputs.decisions == {1: batch}
