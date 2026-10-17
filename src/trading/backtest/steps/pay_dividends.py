# filepath: src/trading/backtest/steps/pay_dividends.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.portfolio.corporate_actions import pay_dividend


class PayDividendsStep:
    """Credit date-only dividend payments after that day's trading and decisions.

    Example:
        context = PayDividendsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Settle gross receivables once, including rights whose original shares were sold.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        for action in context.corporate_actions.payments.get(tick.ts_us, ()):
            entitlement = account.distribution_entitlements.get(action.action_id)
            if entitlement is None or not entitlement.recognized or entitlement.cash_paid:
                continue
            state.corporate_action_records.append(pay_dividend(account, entitlement))
            if not entitlement.shares_qty or entitlement.shares_released:
                del account.distribution_entitlements[action.action_id]
        return context
