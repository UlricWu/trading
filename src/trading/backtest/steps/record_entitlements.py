# filepath: src/trading/backtest/steps/record_entitlements.py
from decimal import Decimal
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.portfolio.corporate_actions import CorporateActionRecord, DistributionEntitlement, recognize_distribution


class RecordEntitlementsStep:
    """Freeze registration-day holdings after all trading and submissions at the close.

    Example:
        context = RecordEntitlementsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Freeze eligibility, then recognize known compensation for subsequent settlement.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        if tick.ts_us == tick.session.close_ts_us:
            account.registration_holdings.setdefault(tick.session.trade_date, dict(account.positions))
        for scheduled in context.corporate_actions.registrations.get(tick.ts_us, ()):
            action = state.distribution_terms.get(scheduled.action_id)
            if action is None or action.benefit_state == "cancelled":
                continue
            if action.action_id in account.distribution_entitlements:
                continue
            if action.cash_per_share == 0 and action.shares_per_share == 0:
                continue
            quantity = account.registration_holdings[tick.session.trade_date].get(action.symbol, 0)
            if not quantity:
                continue
            entitlement = DistributionEntitlement(distribution=action, eligible_qty=quantity)
            account.distribution_entitlements[action.action_id] = entitlement
            state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                action.symbol, "registered", quantity, Decimal(0), Decimal(0), 0, action.source_ref))
            if (action.kind == "compensation" and action.benefit_state == "confirmed"
                and action.cash_per_share is not None and action.shares_per_share is not None):
                state.corporate_action_records.append(recognize_distribution(account, entitlement, ts_us=tick.ts_us))
        return context
