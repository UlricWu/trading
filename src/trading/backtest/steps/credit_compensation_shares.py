# filepath: src/trading/backtest/steps/credit_compensation_shares.py
"""Credit compensation stock after the day's trading, independently of its release."""

from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.corporate_actions import credit_compensation_shares


class CreditCompensationSharesStep:
    """Settle visible, recognized share receivables without generating a trade.

    Example:
        context = CreditCompensationSharesStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Move due stock into the account once, retaining its independent trading lock.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        for entitlement in tuple(account.distribution_entitlements.values()):
            action = entitlement.distribution
            if (action.kind != "compensation" or not entitlement.recognized or entitlement.shares_credited
                or action.shares_credit_ts_us is None or action.shares_credit_ts_us > tick.ts_us):
                continue
            state.corporate_action_records.append(credit_compensation_shares(account, entitlement, ts_us=tick.ts_us))
            if action.announced_ts_us > action.shares_credit_ts_us:
                key = (action.source_ref, action.action_id, "late_compensation_settlement")
                state.corporate_action_issues.setdefault(key, CorporateActionIssue(ts_us=tick.ts_us,
                    symbol=action.symbol, action_id=action.action_id, reason=key[2], source_ref=action.source_ref))
        return context
