# filepath: src/trading/backtest/steps/advance_orders.py
from typing import cast

from src.trading.backtest.context import BacktestContext, TickState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket


class AdvanceOrdersStep:
    """Settle and expire existing orders using only this tick's visible observations.

    Example:
        context = AdvanceOrdersStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Finish old order activity before any new decision sees the account.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        market = cast(ReplayMarket, context.market)
        cast(AccountExecutionSimulator, context.executor).advance(
            ts_us=tick.ts_us, observations=market.observations_at(tick.ts_us),
        )
        return context
