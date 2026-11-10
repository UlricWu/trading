# filepath: src/trading/backtest/steps/apply_distributions.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.corporate_actions import recognize_distribution


class ApplyDistributionsStep:
    """Recognize due rights when their amounts are visible, retaining gaps in the replay.

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
            if (action.ex_ts_us != tick.ts_us or action.benefit_state != "confirmed"
                or (action.reference_cash_per_share is None and action.reference_price is None)):
                continue
            market.apply_distribution_reference(action_id=action.action_id, symbol=action.symbol, ts_us=tick.ts_us,
                trade_date=tick.session.trade_date, reference_cash_per_share=action.reference_cash_per_share,
                reference_shares_per_share=action.reference_shares_per_share, reference_price=action.reference_price)
        for entitlement in tuple(account.distribution_entitlements.values()):
            action = entitlement.distribution
            if (entitlement.recognized or action.benefit_state != "confirmed"
                or (action.kind == "distribution" and (action.ex_ts_us is None or action.ex_ts_us > tick.ts_us))
                or (action.kind == "compensation" and action.record_ts_us >= tick.ts_us)):
                continue
            issue_reason = None
            if action.cash_per_share is None or action.shares_per_share is None:
                issue_reason = "pending_distribution_terms"
            else:
                shares = entitlement.eligible_qty * action.shares_per_share
                if action.kind == "distribution" and shares != shares.to_integral_value():
                    issue_reason = "fractional_shares"
            if issue_reason is not None:
                account.distribution_entitlements[action.action_id] = entitlement.model_copy(
                    update={"recognition_issue": issue_reason})
                issue = CorporateActionIssue(ts_us=tick.ts_us, symbol=action.symbol,
                    action_id=action.action_id, reason=issue_reason, source_ref=action.source_ref)
                state.corporate_action_issues.setdefault((issue.source_ref, issue.action_id, issue.reason), issue)
                continue
            state.corporate_action_records.append(recognize_distribution(account, entitlement, ts_us=tick.ts_us))
        return context
