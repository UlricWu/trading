# filepath: src/trading/backtest/steps/generate_signal.py
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState, TickState
from src.trading.backtest.market.signals import SignalBatch, produce_signal


@dataclass(frozen=True, slots=True)
class GenerateSignalStep:
    """Produce the current decision's prediction and retain its original lineage.

    Example:
        context = GenerateSignalStep(prediction_dir=prediction_dir).run(context)
    """

    prediction_dir: Path

    def run(self, context: BacktestContext) -> BacktestContext:
        """Read only this tick's batch; model inference writes its prediction here.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        inputs = cast(BacktestInputs, context.inputs)
        batch = cast(Mapping[int, SignalBatch], inputs.decisions).get(tick.ts_us)
        if batch is not None:
            signal = produce_signal(batch, prediction_dir=self.prediction_dir)
            tick.signal = signal
            state = cast(BacktestState, context.state)
            state.signal_tape.append(signal.event)
            state.prediction_refs.append(signal.prediction_ref)
            state.decision_dates[tick.ts_us] = signal.trade_date
        return context
