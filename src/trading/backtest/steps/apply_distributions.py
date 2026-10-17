# filepath: src/trading/backtest/steps/apply_distributions.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.corporate_actions import recognize_distribution


class ApplyDistributionsStep:
    """Apply ex-date rights before trading, without inventing execution observations.

    Example:
        context = ApplyDistributionsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Recognize registered benefits and replace stale cum-dividend valuation bases.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        market = cast(ReplayMarket, context.market)
        account = state.portfolio_state
        for scheduled in context.corporate_actions.recognitions.get(tick.ts_us, ()):
            action = state.distribution_terms[scheduled.action_id]
            market.apply_distribution_reference(symbol=action.symbol, ts_us=tick.ts_us,
                trade_date=tick.session.trade_date, reference_cash_per_share=action.reference_cash_per_share,
                reference_shares_per_share=action.reference_shares_per_share)
            entitlement = account.distribution_entitlements.get(action.action_id)
            if entitlement is not None and not entitlement.recognized:
                shares = entitlement.eligible_qty * action.shares_per_share
                if shares != shares.to_integral_value():
                    account.distribution_entitlements[action.action_id] = entitlement.model_copy(
                        update={"recognition_issue": "fractional_shares"})
                    issue = CorporateActionIssue(ts_us=tick.ts_us, symbol=action.symbol,
                        action_id=action.action_id, reason="fractional_shares", source_ref=action.source_ref)
                    state.corporate_action_issues[(issue.source_ref, issue.action_id, issue.reason)] = issue
                    continue
                state.corporate_action_records.append(recognize_distribution(account, entitlement))
        return context
