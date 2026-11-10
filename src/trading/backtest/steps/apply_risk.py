# filepath: src/trading/backtest/steps/apply_risk.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.core.events import TargetEvent
from src.trading.risk import RiskManager
from src.trading.risk.base import RiskContext
from src.trading.risk.evaluation import RiskCase, RiskObservation


class ApplyRiskStep:
    """Freeze the pre-control request and adjust only the current execution target.

    Example:
        context = ApplyRiskStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Check every tick, including current commitments without a new strategy target.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        state = cast(BacktestState, context.state)
        account = state.portfolio_state
        market = cast(ReplayMarket, context.market)
        executor = cast(AccountExecutionSimulator, context.executor)
        equity = state.equity_curve.points[-1]
        case = RiskCase(
            case_id=str(tick.ts_us), trade_date=tick.session.trade_date,
            target=tick.original_target.targets if tick.original_target is not None else None,
            context=RiskContext(
                ts_us=tick.ts_us, prices={symbol: float(price) for symbol, price in market.prices.items()},
                equity=equity.equity, cash=float(account.cash), peak_equity=equity.peak_equity,
                positions=account.positions, pending_orders=tuple(state.pending_orders.values()),
                quantity_steps={symbol: rule.buy_step_qty for symbol, rule in executor.rules.items()},
            ),
        )
        decision = cast(RiskManager, context.risk).apply(target=case.target, ctx=case.context)
        tick.risk_observation = RiskObservation(case, decision)
        tick.execution_target = TargetEvent(tick.ts_us, decision.adjusted) if decision.adjusted is not None else None
        tick.cancel_order_ids = decision.cancel_order_ids
        return context
