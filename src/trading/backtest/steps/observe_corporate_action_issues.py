# filepath: src/trading/backtest/steps/observe_corporate_action_issues.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.backtest.market.replay import ReplayMarket


class ObserveCorporateActionIssuesStep:
    """Keep unresolved coverage and exposed unsupported events visible in the result.

    Example:
        context = ObserveCorporateActionIssuesStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Observe exposure before settlement and after submissions without losing same-tick exits.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        exposed = {symbol for symbol, qty in state.portfolio_state.positions.items() if qty}
        exposed.update(order.intent.symbol for order in state.pending_orders.values())
        exposed.update(item.distribution.symbol for item in state.portfolio_state.distribution_entitlements.values()
            if item.distribution.benefit_state != "cancelled" and (not item.recognized
                or (item.cash_amount != 0 and not item.cash_paid)
                or (item.shares_qty != 0 and not item.shares_released)))
        for issue in context.corporate_actions.issues:
            if issue.ts_us <= tick.ts_us and (issue.symbol is None or issue.symbol in exposed):
                state.corporate_action_issues[(issue.source_ref, issue.action_id, issue.reason)] = issue
        for entitlement in state.portfolio_state.distribution_entitlements.values():
            action = entitlement.distribution
            if (action.kind == "compensation" and entitlement.recognized and entitlement.shares_qty > 0
                and not entitlement.shares_credited and action.shares_credit_ts_us is None
                and action.shares_available_ts_us is not None and action.shares_available_ts_us <= tick.ts_us):
                key = (action.source_ref, action.action_id, "pending_compensation_credit")
                state.corporate_action_issues.setdefault(key, CorporateActionIssue(ts_us=tick.ts_us,
                    symbol=action.symbol, action_id=action.action_id, reason=key[2], source_ref=action.source_ref))
        for action in state.distribution_terms.values():
            if (action.ex_ts_us is None or action.ex_ts_us > tick.ts_us or action.symbol not in exposed
                or action.reference_cash_per_share is not None or action.reference_price is not None):
                continue
            mark = cast(ReplayMarket, context.market).price_marks.get(action.symbol)
            if mark is None or mark.ts_us < action.ex_ts_us:
                key = (action.source_ref, action.action_id, "missing_ex_reference")
                state.corporate_action_issues.setdefault(key, CorporateActionIssue(ts_us=tick.ts_us,
                    symbol=action.symbol, action_id=action.action_id, reason=key[2], source_ref=action.source_ref))
        for notice in context.corporate_actions.notices:
            if max(notice.available_ts_us, notice.effective_ts_us) <= tick.ts_us and notice.symbol in exposed:
                key = (notice.source_ref, notice.action_id, f"unsupported_{notice.kind}")
                if key not in state.corporate_action_issues:
                    state.corporate_action_issues[key] = CorporateActionIssue(ts_us=tick.ts_us,
                        symbol=notice.symbol, action_id=notice.action_id, reason=key[2], source_ref=notice.source_ref)
        return context
