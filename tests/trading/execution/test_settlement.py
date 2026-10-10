# filepath: tests/trading/execution/test_settlement.py
from decimal import Decimal
from dataclasses import replace

import pytest

from src.trading.core.events import FillEvent, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.settlement import SettlementEngine
from src.trading.portfolio.state import PortfolioState


def test_repeated_fill_changes_account_and_ledger_once() -> None:
    account = PortfolioState(initial_cash=20_000, cash=20_000)
    ledger = ExecutionLedger()
    settlement = SettlementEngine()
    fill = FillEvent(ts_us=1, symbol="600000", side=Side.BUY,
                     qty=100, price=10, order_id=1, fill_id="fill-1",
                     observation_id="minute-1", reference_price=10)
    assert settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger) is True
    assert settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger) is False
    assert account.cash == 19_000
    assert account.positions == {"600000": 100}
    assert len(ledger.records) == 1


def test_oversell_fails_before_changing_account() -> None:
    account = PortfolioState(initial_cash=0, cash=0, positions={"600000": 100},
                             avg_cost={"600000": 10})
    ledger = ExecutionLedger()
    fill = FillEvent(ts_us=1, symbol="600000", side=Side.SELL,
                     qty=200, price=10, order_id=1, fill_id="fill-1",
                     observation_id="minute-1", reference_price=10)
    with pytest.raises(ValueError, match="position"):
        SettlementEngine().settle_fill(fill=fill,
                                      portfolio=account, ledger=ledger)
    assert account.cash == 0
    assert account.positions == {"600000": 100}
    assert ledger.records == []


def test_distinct_fill_identities_with_equal_values_both_settle() -> None:
    account = PortfolioState(20_000, 20_000)
    ledger = ExecutionLedger()
    settlement = SettlementEngine()
    first = FillEvent(1, "600000", Side.BUY, 100, Decimal("10"), 1,
                      "first", "minute-1", Decimal("10"))
    second = replace(first, fill_id="second")
    assert settlement.settle_fill(fill=first, portfolio=account, ledger=ledger)
    assert settlement.settle_fill(fill=second, portfolio=account, ledger=ledger)
    assert account.cash == 18_000
    assert account.positions == {"600000": 200}
    assert len(ledger.records) == 2


@pytest.mark.parametrize("changes", [{"price": Decimal("11")}, {"qty": 101},
                                    {"reference_price": Decimal("9")}])
def test_conflicting_identity_fails_before_all_side_effects(changes: dict[str, object]) -> None:
    account = PortfolioState(20_000, 20_000)
    ledger = ExecutionLedger()
    settlement = SettlementEngine()
    fill = FillEvent(1, "600000", Side.BUY, 100, Decimal("10"), 1,
                     "first", "minute-1", Decimal("10"))
    settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger)
    with pytest.raises(ValueError, match="conflicting fill_id"):
        settlement.settle_fill(fill=replace(fill, **changes), portfolio=account, ledger=ledger)
    assert account.cash == 19_000
    assert account.positions == {"600000": 100}
    assert len(ledger.records) == 1


def test_exact_decimal_cash_and_ledger_survive_many_small_fills() -> None:
    account = PortfolioState(Decimal("1"), Decimal("1"))
    ledger = ExecutionLedger()
    settlement = SettlementEngine()
    for index in range(10):
        fill = FillEvent(index, "600000", Side.BUY, 1, Decimal("0.1"), 1,
                         str(index), str(index), Decimal("0.1"))
        settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger)
    assert account.cash == Decimal("0")
    assert ledger.order_turnover[1] == Decimal("1")
    assert all(row["price"] == "0.1" for row in ledger.records)
