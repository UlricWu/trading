# filepath: tests/trading/risk/test_engine.py
from decimal import Decimal

import pytest

from src.trading.core.events import OrderIntent, PendingOrder, Side
from src.trading.risk.base import RiskContext
from src.trading.risk.engine import RiskManager
from src.trading.risk.rules.max_drawdown import MaxDrawdownRule
from src.trading.risk.rules.max_position import MaxGrossExposureRule, MaxSingleNameNotionalRule


def test_timer_does_not_restore_expired_buy_or_undo_existing_sell() -> None:
    risk = RiskManager((MaxSingleNameNotionalRule(0.2),))
    context = RiskContext(4, {"A": 10.0}, 20000, 16400, 20000, {"A": 360}, (), {"A": 1})
    assert risk.apply(target=None, ctx=context).adjusted is None
    sell = OrderIntent(1, "A", Side.SELL, 400, 1, 1, 10, Decimal("10"))
    pending = PendingOrder(sell, 400, Decimal(0), 400, Decimal("10"))
    context = RiskContext(4, {"A": 10.0}, 20000, 14000, 20000, {"A": 600}, (pending,), {"A": 1})
    assert risk.apply(target=None, ctx=context).adjusted is None


def test_shrink_buy_keeps_cancellation_and_each_rule_observation() -> None:
    order = OrderIntent(1, "A", Side.BUY, 400, 1, 1, 10, Decimal("10"))
    pending = PendingOrder(order, 400, Decimal(4000), 0, Decimal("10"))
    context = RiskContext(2, {"A": 10.0}, 20000, 14000, 20000, {"A": 600}, (pending,), {"A": 100})
    risk = RiskManager((MaxSingleNameNotionalRule(0.4), MaxGrossExposureRule(1)))
    decision = risk.apply(target=None, ctx=context)
    assert decision.adjusted == {"A": 800}
    assert decision.cancel_order_ids == (1,)
    assert [item.triggered for item in decision.rules] == [True, False]
    assert context.positions == {"A": 600}


def test_unfilled_sell_does_not_finance_risk_budget() -> None:
    context = RiskContext(2, {"A": 10.0, "B": 10.0}, 20000, 0, 20000,
                          {"A": 1000}, (), {"A": 100, "B": 100})
    decision = RiskManager((MaxGrossExposureRule(0.5),)).apply(target={"A": 0, "B": 1000}, ctx=context)
    assert decision.adjusted == {"A": 0, "B": 0}


def test_zero_equity_rejects_new_buys_and_cancels_pending_buys() -> None:
    order = OrderIntent(1, "A", Side.BUY, 100, 1, 1, 10, Decimal("10"))
    pending = PendingOrder(order, 100, Decimal(1000), 0, Decimal("10"))
    context = RiskContext(2, {"A": 10.0}, 0, 0, 20000, {}, (pending,), {"A": 100})
    decision = RiskManager((MaxSingleNameNotionalRule(0.2),)).apply(target={"A": 100}, ctx=context)
    assert decision.adjusted is None
    assert decision.cancel_order_ids == (1,)
    assert decision.reason == "rejected"


def test_drawdown_produces_explicit_liquidation_and_never_changes_account() -> None:
    context = RiskContext(2, {"A": 10.0}, 8000, 0, 10000, {"A": 800}, (), {"A": 100})
    decision = RiskManager((MaxDrawdownRule(0.1),)).apply(target=None, ctx=context)
    assert decision.adjusted == {"A": 0}
    assert context.positions == {"A": 800}


def test_missing_price_and_empty_rules_fail() -> None:
    with pytest.raises(ValueError, match="nonempty"):
        RiskManager(())
    context = RiskContext(1, {}, 20000, 20000, 20000, {}, (), {"A": 100})
    with pytest.raises(ValueError, match="prices"):
        RiskManager((MaxSingleNameNotionalRule(0.2),)).apply(target={"A": 100}, ctx=context)


@pytest.mark.parametrize('equity', [9000, 0, -100])
def test_drawdown_triggers_at_the_exact_boundary_and_after_nonpositive_equity(equity: int) -> None:
    context = RiskContext(2, {'A': 10.0}, equity, 0, 10000, {'A': 800}, (), {'A': 100})
    decision = RiskManager((MaxSingleNameNotionalRule(.2), MaxDrawdownRule(.1))).apply(target=None, ctx=context)
    assert decision.adjusted == {'A': 0}
    assert decision.rules[-1].triggered
