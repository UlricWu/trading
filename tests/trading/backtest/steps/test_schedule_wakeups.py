# filepath: tests/trading/backtest/steps/test_schedule_wakeups.py
from decimal import Decimal

from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.steps.schedule_wakeups import ScheduleWakeupsStep
from src.trading.core.events import OrderIntent, Side
from src.trading.core.time import US_PER_MINUTE

M = US_PER_MINUTE


def test_pending_deadlines_are_merged_with_clock_times_and_clipped_at_the_end(tick_context: BacktestContext) -> None:
    orders = tuple(OrderIntent(0, symbol, Side.BUY, 1, order_id, 0, expiry, Decimal('10'))
                   for order_id, symbol, expiry in ((1, 'A', 3 * M), (2, 'B', 2 * M), (3, 'A', 3 * M), (4, 'B', 5 * M)))
    tick_context.executor.submit_orders(orders=orders)
    tick_context.clock = ReplayClock.from_iterable((4 * M,), end_us=4 * M)
    assert ScheduleWakeupsStep().run(tick_context) is tick_context
    assert tuple(tick_context.clock) == (2 * M, 3 * M, 4 * M)
