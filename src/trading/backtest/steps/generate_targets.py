# filepath: src/trading/backtest/steps/generate_targets.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.core.events import TargetEvent
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.portfolio.engine import make_executable_targets


class GenerateTargetsStep:
    """Record the original strategy target and make it the default execution request.

    Example:
        context = GenerateTargetsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Advance fixed holding without a signal; preserve missing-score commitments.

        Example:
            context = step.run(context)
        """
        tick = cast(TickState, context.tick)
        state = cast(BacktestState, context.state)
        account = state.portfolio_state
        market = cast(ReplayMarket, context.market)
        constructor = cast(PortfolioConstructor | FixedHoldingConstructor, context.constructor)
        signal = tick.signal
        target = None
        if isinstance(constructor, FixedHoldingConstructor):
            target = constructor.targets(
                ts_us=tick.ts_us, trade_date=tick.session.trade_date,
                scores=signal.event.scores if signal is not None else None,
                state=account, prices=market.prices, pending_orders=tuple(state.pending_orders.values()),
            )
        elif signal is not None:
            raw_targets = constructor.targets(ts_us=tick.ts_us, scores=signal.event.scores, state=account)
            targets = make_executable_targets(
                raw_targets=raw_targets, positions=account.positions,
                current_raw_prices={symbol: float(price) for symbol, price in market.prices.items()},
                hold_symbols=set(signal.symbols) - signal.event.scores.keys(),
                pending_orders=tuple(state.pending_orders.values()),
                max_positions=constructor.max_positions if isinstance(constructor, TopKHysteresisConstructor) else None,
            )
            target = TargetEvent(tick.ts_us, targets)
        tick.original_target = target
        tick.execution_target = target
        if target is not None:
            state.target_tape.append(target)
        return context
