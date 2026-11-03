# filepath: src/trading/backtest/steps/submit_frozen_commands.py
from typing import cast

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs, TickState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.core.events import OrderIntent


class SubmitFrozenCommandsStep:
    """Preserve frozen quantities and same-time submission/cancellation order.

    Example:
        context = SubmitFrozenCommandsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Submit prepared commands without constructing strategy targets.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        source = cast(OrderCommandSchedule, cast(BacktestInputs, context.inputs).decisions)
        executor = cast(AccountExecutionSimulator, context.executor)
        for command in source.at(tick.ts_us):
            if isinstance(command, OrderIntent):
                executor.submit_orders(orders=(command,))
            else:
                executor.cancel_orders(ts_us=command.ts_us, order_ids=command.order_ids)
        return context
