# filepath: src/trading/backtest/steps/submit_targets.py
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState, TickState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.core.events import OrderTerms, Side, TargetEvent
from src.trading.core.time import TradingWindow, advance_trading_minutes


@dataclass(frozen=True, slots=True)
class SubmitTargetsStep:
    """Submit the execution target or cancellations while preserving existing order terms.

    Example:
        context = SubmitTargetsStep(order_window_minutes=2).run(context)
    """

    order_window_minutes: int

    def run(self, context: BacktestContext) -> BacktestContext:
        """Generate terms only for additions; never reissue an absent current target.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        executor = cast(AccountExecutionSimulator, context.executor)
        if tick.execution_target is not None:
            inputs = cast(BacktestInputs, context.inputs)
            terms = build_new_order_terms(target=tick.execution_target, state=cast(BacktestState, context.state),
                order_window_minutes=self.order_window_minutes,
                windows=tuple(window for session in inputs.market.sessions for window in session.windows))
            executor.submit_targets(target=tick.execution_target, cancel_order_ids=tick.cancel_order_ids,
                                    terms=terms, market=cast(ReplayMarket, context.market))
        elif tick.cancel_order_ids:
            executor.cancel_orders(ts_us=tick.ts_us, order_ids=tick.cancel_order_ids)
        return context


def build_new_order_terms(
    *, target: TargetEvent, state: BacktestState,
    order_window_minutes: int, windows: Sequence[TradingWindow],
) -> dict[str, OrderTerms]:
    """Supply fresh deadlines only for quantities not covered by same-side orders.

    Shrinking pending orders retains their original price and expiry during reconciliation;
    it must not require calendar coverage for a new order window.

    Example:
        terms = build_new_order_terms(target=target, state=state,
            order_window_minutes=2, windows=windows)
    """
    new_symbols = []
    for symbol, quantity in target.targets.items():
        delta = quantity - state.portfolio_state.positions.get(symbol, 0)
        side = Side.BUY if delta > 0 else Side.SELL
        covered = sum(order.remaining_qty for order in state.pending_orders.values()
                      if order.intent.symbol == symbol and order.intent.side == side)
        if abs(delta) > covered:
            new_symbols.append(symbol)
    if not new_symbols:
        return {}
    expiry = advance_trading_minutes(start_ts_us=target.ts_us, minutes=order_window_minutes, windows=windows)
    return {symbol: OrderTerms(target.ts_us, expiry) for symbol in new_symbols}
