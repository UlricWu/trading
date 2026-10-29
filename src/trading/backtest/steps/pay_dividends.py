# filepath: src/trading/backtest/steps/pay_dividends.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionIssue
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
        for entitlement in tuple(account.distribution_entitlements.values()):
            action = entitlement.distribution
            if (not entitlement.recognized or entitlement.cash_paid or action.pay_ts_us is None
                or action.pay_ts_us > tick.ts_us):
                continue
            state.corporate_action_records.append(pay_dividend(account, entitlement, ts_us=tick.ts_us))
            if action.kind == "compensation" and action.announced_ts_us > action.pay_ts_us:
                key = (action.source_ref, action.action_id, "late_compensation_settlement")
                state.corporate_action_issues.setdefault(key, CorporateActionIssue(ts_us=tick.ts_us,
                    symbol=action.symbol, action_id=action.action_id, reason=key[2], source_ref=action.source_ref))
        return context
