# filepath: tests/trading/backtest/steps/test_generate_signal.py
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.trading.backtest.context import BacktestContext, BacktestInputs, TickState
from src.trading.backtest.market.signals import RecordedSignalBatch, produce_signal
from src.trading.backtest.steps.generate_signal import GenerateSignalStep
from src.trading.core.events import SignalEvent
from src.trading.core.time import US_PER_MINUTE

M = US_PER_MINUTE


def test_signal_step_consumes_only_current_batch_and_records_lineage_once(
    tick_context: BacktestContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = RecordedSignalBatch('2026-07-03', SignalEvent(M, {'A': .8}), ('A', 'B'), tmp_path / 'first.parquet')
    last = RecordedSignalBatch('2026-07-03', SignalEvent(2 * M, {'B': .7}), ('A', 'B'), tmp_path / 'last.parquet')
    batches = {M: first, 2 * M: last}
    tick_context.inputs = BacktestInputs(tick_context.inputs.market, batches)
    batches.clear()
    produce = Mock(wraps=produce_signal)
    monkeypatch.setattr('src.trading.backtest.steps.generate_signal.produce_signal', produce)
    step = GenerateSignalStep(prediction_dir=tmp_path / 'produced')

    for ts_us, expected, count in ((M - 1, None, 0), (M, first, 1), (M + 1, None, 1), (2 * M, last, 2)):
        tick_context.tick = TickState(ts_us=ts_us, session=tick_context.inputs.market.session_at(ts_us))
        assert step.run(tick_context) is tick_context
        assert tick_context.tick.signal is expected
        assert produce.call_count == count
        assert tick_context.state.prediction_refs == [first.prediction_ref, last.prediction_ref][:count]
    assert tick_context.state.signal_tape.events == [first.event, last.event]
    assert tick_context.state.decision_dates == {M: '2026-07-03', 2 * M: '2026-07-03'}
    assert not step.prediction_dir.exists()


def test_signal_failure_does_not_record_a_batch(
    tick_context: BacktestContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(M, {'A': .8}), ('A',), tmp_path / 'prediction')
    tick_context.inputs = BacktestInputs(tick_context.inputs.market, {M: batch})
    error = OSError('prediction write unavailable')
    monkeypatch.setattr('src.trading.backtest.steps.generate_signal.produce_signal', Mock(side_effect=error))
    with pytest.raises(OSError) as caught:
        GenerateSignalStep(prediction_dir=tmp_path).run(tick_context)
    assert caught.value is error
    assert tick_context.tick.signal is None
    assert tick_context.state.prediction_refs == tick_context.state.signal_tape.events == []
