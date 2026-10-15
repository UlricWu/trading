# filepath: src/trading/backtest/steps/value_account.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.portfolio.engine import mark_to_market


class ValueAccountStep:
    """Value the current account, retaining initial capital and official closing samples.

    The same Step runs before decisions and after submission. Same-time equity is
    replaced by the final value, while initial equity and the existing peak survive.

    Example:
        context = ValueAccountStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Require declared raw close marks for all holdings at the official close.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        market = cast(ReplayMarket, context.market)
        tick = cast(TickState, context.tick)
        ts_us, session = tick.ts_us, tick.session
        prices = {symbol: float(price) for symbol, price in market.prices.items()}
        valuation = mark_to_market(positions=state.portfolio_state.positions,
            current_raw_prices=prices, last_mark_prices=state.last_mark_prices)
        state.last_mark_prices = valuation.mark_prices
        if state.initial_equity is None:
            state.initial_equity = float(state.portfolio_state.cash) + valuation.market_value
            state.equity_curve.starting_peak_equity = max(state.equity_curve.starting_peak_equity, state.initial_equity)
        state.equity_curve.append(ts_us=ts_us, cash=float(state.portfolio_state.cash), market_value=valuation.market_value)
        if ts_us == session.close_ts_us:
            close_symbols = market.daily_close_symbols.get(session.close_ts_us, set())
            if not state.portfolio_state.positions.keys() <= close_symbols:
                raise ValueError("end-of-day holdings require declared raw daily-close marks")
            state.daily_equity[session.trade_date] = state.equity_curve.points[-1]
        return context
