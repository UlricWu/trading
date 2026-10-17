# filepath: src/trading/backtest/steps/update_distribution_terms.py
"""Expose announced terms without changing historical registration quantities."""
from decimal import Decimal
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.portfolio.corporate_actions import CorporateActionRecord


class UpdateDistributionTermsStep:
    """Apply newly visible versions to one account's outstanding rights.

    Example:
        context = UpdateDistributionTermsStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Change terms once; retain registered quantities and previously completed stages.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        tick = cast(TickState, context.tick)
        account = state.portfolio_state
        for action in context.corporate_actions.versions:
            if action.announced_ts_us > tick.ts_us:
                break
            current = state.distribution_terms.get(action.action_id)
            if current is not None and current.announced_ts_us >= action.announced_ts_us:
                continue
            state.distribution_terms[action.action_id] = action
            entitlement = account.distribution_entitlements.get(action.action_id)
            if entitlement is not None:
                account.distribution_entitlements[action.action_id] = entitlement.model_copy(update={"distribution": action})
            state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                action.symbol, "terms_updated", entitlement.eligible_qty if entitlement is not None else 0,
                Decimal(0), Decimal(0), 0, action.source_ref))
        return context
