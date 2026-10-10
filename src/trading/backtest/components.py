# filepath: src/trading/backtest/components.py
from __future__ import annotations

from src.access import Access
from src.config.backtest_config import (
    FakeExecutionConfig, InitialAccount, RiskConfig, SingleNameRiskConfig, GrossRiskConfig,
)
from src.trading.backtest.context import BacktestState
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.core.equity import EquityCurve
from src.trading.portfolio.state import PortfolioState
from src.trading.risk.base import RiskRule
from src.trading.risk.engine import RiskManager
from src.trading.risk.rules.max_drawdown import MaxDrawdownRule
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule, MaxGrossExposureRule


def prepare_market(*, config: FakeExecutionConfig, initial: InitialAccount, access: Access) -> ReplayMarket:
    """Validate normalized input coverage against the formal account calendar.

    Example:
        market = prepare_market(config=execution_config, initial=account_snapshot, access=access)
    """
    source = ReplayMarketInput.model_validate_json(config.market_ref.read_bytes(), strict=True)
    actual = access.trade_dates(start_date=source.sessions[0].trade_date, end_date=source.sessions[-1].trade_date)
    if tuple(actual) != tuple(session.trade_date for session in source.sessions):
        raise ValueError("prepared account sessions must exactly cover the formal trading calendar")
    market = ReplayMarket(source)
    if market.session_at(initial.ts_us).trade_date != initial.trade_date:
        raise ValueError("initial snapshot trade_date disagrees with replay start")
    market.session_at(config.end_ts_us)
    for ts_us in config.risk_check_ts_us:
        market.session_at(ts_us)
        if not initial.ts_us <= ts_us <= config.end_ts_us:
            raise ValueError("risk check is outside the simulation interval")
    if config.slippage_bp is not None and any(o.volume_qty is None for o in source.observations):
        raise ValueError("price-model execution requires explicit observed volume_qty")
    symbols = {o.symbol for o in source.observations} | initial.positions.keys()
    if not symbols <= config.quantity_rules.keys():
        raise ValueError("execution quantity rules must cover all observed and held symbols")
    return market


def create_state(initial: InitialAccount) -> BacktestState:
    """Give this run its own account while retaining same-day sellable restrictions.

    Example:
        state = create_state(initial_account)
    """
    account = PortfolioState(initial_cash=initial.cash, cash=initial.cash,
        positions=initial.positions, avg_cost=initial.average_cost,
        buy_qty_today={s: qty - initial.sellable[s] for s, qty in initial.positions.items()},
        current_day=initial.trade_date)
    return BacktestState(account, equity_curve=EquityCurve(starting_peak_equity=initial.peak_equity))


def build_risk(config: RiskConfig) -> RiskManager:
    """Construct the explicitly selected rule order without a registry or NoOp fallback.

    Example:
        risk = build_risk(risk_config)
    """
    rules: list[RiskRule] = []
    for rule in config.rules:
        if isinstance(rule, SingleNameRiskConfig):
            rules.append(MaxSingleNameNotionalRule(rule.max_ratio))
        elif isinstance(rule, GrossRiskConfig):
            rules.append(MaxGrossExposureRule(rule.max_gross_ratio))
        else:
            rules.append(MaxDrawdownRule(rule.max_drawdown))
    return RiskManager(tuple(rules))
