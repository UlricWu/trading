# filepath: src/trading/backtest/steps/update_market.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket


class UpdateMarketStep:
    """Expose current market facts and release the previous day's T+1 holdings.

    Example:
        context = UpdateMarketStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Advance market visibility and the account day together before settlement.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        cast(ReplayMarket, context.market).on_time(tick.ts_us)
        cast(BacktestState, context.state).portfolio_state.on_trading_day_start(tick.session.trade_date)
        return context
