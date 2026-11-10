# filepath: tests/trading/portfolio/test_state.py
from src.trading.portfolio.state import PortfolioState
from src.trading.core.events import FillEvent, Side
from src.trading.portfolio.state import FillCost
from src.trading.portfolio.corporate_actions import Distribution, DistributionEntitlement
from decimal import Decimal


def test_same_day_wakeup_preserves_snapshot_sellability() -> None:
    account = PortfolioState(initial_cash=0, cash=0, positions={"600000": 100})
    account.on_trading_day_start("2026-07-03")
    account.buy_qty_today["600000"] = 100
    account.on_trading_day_start("2026-07-03")
    assert account.t1_sellable("600000") == 0
    account.on_trading_day_start("2026-07-06")
    assert account.t1_sellable("600000") == 100


def test_t0_sales_never_reclassify_unlisted_bonus_shares_as_same_day_buys() -> None:
    action = Distribution(action_id="bonus", symbol="600000", source_ref="test",
        announced_ts_us=1, record_ts_us=2, ex_ts_us=3, shares_available_ts_us=10,
        cash_per_share=0, shares_per_share="0.5", reference_cash_per_share=0, reference_shares_per_share="0.5")
    account = PortfolioState(0, 0, positions={"600000": 400}, avg_cost={"600000": 5},
        buy_qty_today={"600000": 100}, current_day="2026-07-03",
        distribution_entitlements={"bonus": DistributionEntitlement(
            distribution=action, eligible_qty=200, recognized=True)})
    account.apply_fill(FillEvent(4, "600000", Side.SELL, 300, Decimal(5), 1,
        "fill", "bar", Decimal(5)), cost=FillCost(Decimal(0), Decimal(0), Decimal(0)), meta_out={})
    assert account.positions == {"600000": 100}
    assert account.buy_qty_today == {"600000": 0}
    assert account.t1_sellable("600000") == 0
    account.on_trading_day_start("2026-07-06")
    assert account.unlisted_shares("600000") == 100
    assert account.t1_sellable("600000") == 0


def test_sell_accounting_preserves_quantity_invariants_for_an_explicit_t0_scenario() -> None:
    account = PortfolioState(0, 0, positions={"600000": 200}, avg_cost={"600000": 10},
        buy_qty_today={"600000": 100}, current_day="2026-07-03")
    account.apply_fill(FillEvent(1, "600000", Side.SELL, 150, Decimal("10"), 1,
        "fill-1", "bar-1", Decimal("10")), cost=FillCost(Decimal("0"), Decimal("0"), Decimal("0")),
        meta_out={})
    assert account.positions == {"600000": 50}
    assert account.buy_qty_today == {"600000": 50}
    assert account.t1_sellable("600000") == 0
