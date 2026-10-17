# filepath: src/trading/backtest/steps/record_entitlements.py
from decimal import Decimal
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.portfolio.corporate_actions import CorporateActionRecord, DistributionEntitlement


class RecordEntitlementsStep:
    """Freeze registration-day holdings after all trading and submissions at the close.

    Example:
        context = RecordEntitlementsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Record rights independently of later sales; do not increase cum-dividend NAV.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        for scheduled in context.corporate_actions.registrations.get(tick.ts_us, ()):
            action = state.distribution_terms[scheduled.action_id]
            if action.action_id in account.distribution_entitlements:
                continue
            if not action.cash_per_share and not action.shares_per_share:
                continue
            quantity = account.positions.get(action.symbol, 0)
            if not quantity:
                continue
            account.distribution_entitlements[action.action_id] = DistributionEntitlement(
                distribution=action, eligible_qty=quantity)
            state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                action.symbol, "registered", quantity, Decimal(0), Decimal(0), 0, action.source_ref))
        return context
