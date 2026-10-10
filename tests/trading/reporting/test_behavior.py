# filepath: tests/trading/reporting/test_behavior.py
from decimal import Decimal

from src.trading.core.equity import EquityCurve
from src.trading.core.events import FillEvent, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.settlement import SettlementEngine
from src.trading.portfolio.state import PortfolioState
from src.trading.reporting.behavior import BehaviorReport


def test_two_fills_complete_one_order_instead_of_reporting_two_hundred_percent() -> None:
    ledger = ExecutionLedger()
    ledger.record_order_submit(ts_us=0, symbol="A", side="BUY", qty=200, order_id=1)
    account = PortfolioState(10_000, 10_000)
    curve = EquityCurve()
    curve.append(ts_us=0, cash=10_000, market_value=0)
    for index in (1, 2):
        fill = FillEvent(index, "A", Side.BUY, 100, Decimal("10"), 1,
                         str(index), str(index), Decimal("10"))
        SettlementEngine().settle_fill(fill=fill, portfolio=account, ledger=ledger)
        summary = BehaviorReport(ledger, curve).summary()
        assert summary["qty_fill_rate"] == index / 2
        assert summary["fill_rate"] == (1 if index == 2 else 0)
    assert summary["fills"] == 2
    assert summary["completed_orders"] == 1


def test_no_orders_has_undefined_completion_instead_of_a_successful_zero() -> None:
    summary = BehaviorReport(ExecutionLedger(), EquityCurve()).summary()
    assert summary["qty_fill_rate"] is None
    assert summary["fill_rate"] is None
    assert summary["reject_rate"] is None
