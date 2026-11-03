# filepath: tests/trading/portfolio/constructors/test_fixed_holding.py
from datetime import date, time
from decimal import Decimal

from src.trading.core.events import OrderIntent, PendingOrder, Side
from src.trading.orders.constraints import QuantityRules
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.state import PortfolioState
from src.utils.datetime_utils import DateTimeUtils


def _ts(day: str, hour: int) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(hour), date.fromisoformat(day))


def test_fixed_list_ranking_ties_equal_slots_and_missing_candidates_keep_cash() -> None:
    rule = QuantityRules(100, 100, 1, 1, 1, Decimal('.01'), True)
    strategy = FixedHoldingConstructor(3, time(14), time(13), ('2026-07-03', '2026-07-06'),
                                       {'A': rule, 'B': rule}, {})
    account = PortfolioState(10000, 10000, current_day='2026-07-03')
    result = strategy.targets(ts_us=_ts('2026-07-03', 14), trade_date='2026-07-03',
        scores={'B': 1, 'A': 1}, state=account, prices={'A': Decimal(10), 'B': Decimal(10)}, pending_orders=())
    assert list(result.targets) == ['A', 'B']
    assert dict(result.targets) == {'A': 300, 'B': 300}
    assert strategy.wakeups() == (_ts('2026-07-06', 13),)
    assert account.cash == 10000


def test_carryover_and_reserved_buys_consume_slots_and_unsold_proceeds_are_unavailable() -> None:
    rule = QuantityRules(1, 1, 1, 1, 1, Decimal('.01'), True)
    due = _ts('2026-07-03', 13)
    strategy = FixedHoldingConstructor(2, time(14), time(13), ('2026-07-03', '2026-07-06'),
                                       {'A': rule, 'B': rule, 'C': rule}, {'A': due})
    account = PortfolioState(2000, 500, positions={'A': 100}, avg_cost={'A': Decimal(10)}, current_day='2026-07-03')
    result = strategy.targets(ts_us=due, trade_date='2026-07-03', scores=None,
        state=account, prices={'A': Decimal(10)}, pending_orders=())
    assert result.targets == {'A': 0}
    order = OrderIntent(due, 'A', Side.SELL, 100, 1, due, due + 10_000_000_000, Decimal(10))
    pending = PendingOrder(order, 100, Decimal(0), 100, Decimal(10))
    result = strategy.targets(ts_us=_ts('2026-07-03', 14), trade_date='2026-07-03', scores={'C': 2, 'B': 1},
        state=account, prices={'A': Decimal(10), 'B': Decimal(10), 'C': Decimal(10)}, pending_orders=(pending,))
    assert result.targets == {'A': 0, 'C': 50}
    assert account.positions == {'A': 100} and account.cash == 500
