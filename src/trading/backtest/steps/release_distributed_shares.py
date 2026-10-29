# filepath: src/trading/backtest/steps/release_distributed_shares.py
from decimal import Decimal
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.portfolio.corporate_actions import CorporateActionRecord


class ReleaseDistributedSharesStep:
    """Unlock distributed stock at its listing time, independently of ordinary T+1 buys.

    Example:
        context = ReleaseDistributedSharesStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Release existing allocated shares without changing NAV or creating a fill.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        for entitlement in tuple(account.distribution_entitlements.values()):
            action = entitlement.distribution
            if (not entitlement.recognized or entitlement.shares_released or action.shares_available_ts_us is None
                or action.shares_available_ts_us > tick.ts_us
                or (action.kind == "compensation" and not entitlement.shares_credited)):
                continue
            account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"shares_released": True})
            state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                action.symbol, "shares_released", entitlement.eligible_qty, Decimal(0), Decimal(0), 0, action.source_ref))
        return context
