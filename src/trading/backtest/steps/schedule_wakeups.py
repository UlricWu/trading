# filepath: src/trading/backtest/steps/schedule_wakeups.py
from typing import cast

from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor


class ScheduleWakeupsStep:
    """Register unique order expiries and fixed holding deadlines on this run's clock.

    Example:
        context = ScheduleWakeupsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Keep dynamic order and strategy activity visible without repeating signals.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        wakeups = {order.intent.expires_ts_us for order in state.pending_orders.values()}
        if isinstance(context.constructor, FixedHoldingConstructor):
            wakeups.update(context.constructor.wakeups())
        clock = cast(ReplayClock, context.clock)
        for wakeup in sorted(wakeups):
            clock.schedule(ts_us=wakeup)
        return context
