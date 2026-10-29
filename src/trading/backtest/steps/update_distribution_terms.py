# filepath: src/trading/backtest/steps/update_distribution_terms.py
"""Expose announced terms without changing historical registration quantities."""
from decimal import Decimal
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.corporate_actions import CorporateActionRecord, DistributionEntitlement
from src.utils.datetime_utils import DateTimeUtils


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
            if (entitlement is None and action.record_ts_us < tick.ts_us
                and (action.cash_per_share != 0 or action.shares_per_share != 0)
                and action.benefit_state != "cancelled"):
                day = DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(action.record_ts_us)).date().isoformat()
                holdings = account.registration_holdings.get(day)
                if holdings is None:
                    issue = CorporateActionIssue(ts_us=tick.ts_us, symbol=action.symbol,
                        action_id=action.action_id, reason="missing_registration_holdings", source_ref=action.source_ref)
                    state.corporate_action_issues.setdefault((issue.source_ref, issue.action_id, issue.reason), issue)
                elif quantity := holdings.get(action.symbol, 0):
                    entitlement = DistributionEntitlement(distribution=action, eligible_qty=quantity)
                    account.distribution_entitlements[action.action_id] = entitlement
                    state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                        action.symbol, "registered", quantity, Decimal(0), Decimal(0), 0, action.source_ref))
            if entitlement is not None:
                if entitlement.recognized and action.benefit_state != "confirmed":
                    raise ValueError("recognized entitlement cancellation requires an explicit account adjustment")
                if action.kind == "distribution" and entitlement.recognized and (
                    any(getattr(action, field) != getattr(entitlement.distribution, field)
                        for field in ("cash_per_share", "shares_per_share", "record_ts_us", "ex_ts_us"))
                    or (entitlement.cash_paid and action.pay_ts_us != entitlement.distribution.pay_ts_us)
                    or (entitlement.shares_released and action.shares_available_ts_us != entitlement.distribution.shares_available_ts_us)
                ):
                    raise ValueError("settled distribution correction requires an explicit account adjustment")
                if action.kind == "compensation" and entitlement.recognized:
                    revised = entitlement.model_copy(update={"distribution": action})
                    cash_delta = revised.cash_amount - entitlement.cash_amount
                    shares_delta = revised.shares_qty - entitlement.shares_qty
                    if ((cash_delta and entitlement.cash_paid) or (shares_delta and entitlement.shares_credited)
                        or any(settled and old != new for settled, old, new in (
                            (entitlement.cash_paid, entitlement.distribution.pay_ts_us, action.pay_ts_us),
                            (entitlement.shares_credited, entitlement.distribution.shares_credit_ts_us, action.shares_credit_ts_us),
                            (entitlement.shares_released, entitlement.distribution.shares_available_ts_us, action.shares_available_ts_us),
                        ))):
                        raise ValueError("settled compensation correction requires an explicit account adjustment")
                    if cash_delta or shares_delta:
                        state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                            action.symbol, "entitlement_revised", entitlement.eligible_qty, Decimal(0), cash_delta,
                            0, action.source_ref, shares_delta,
                            revised.theoretical_shares_qty - revised.shares_qty,
                            "account_result" if revised.actual_shares_qty is not None
                            else "exact" if revised.theoretical_shares_qty == revised.shares_qty else "floor_assumption"))
                issue = entitlement.recognition_issue
                if issue == "pending_distribution_terms" and action.cash_per_share is not None and action.shares_per_share is not None:
                    issue = None
                account.distribution_entitlements[action.action_id] = entitlement.model_copy(
                    update={"distribution": action, "recognition_issue": issue})
            state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                action.symbol, "terms_updated", entitlement.eligible_qty if entitlement is not None else 0,
                Decimal(0), Decimal(0), 0, action.source_ref))
            if (action.benefit_state == "confirmed" and action.ex_ts_us is not None and action.announced_ts_us > action.ex_ts_us
                and (action.reference_cash_per_share is not None or action.reference_price is not None)) and cast(
                ReplayMarket, context.market
            ).revise_distribution_reference(action_id=action.action_id, symbol=action.symbol, ts_us=tick.ts_us,
                ex_ts_us=action.ex_ts_us, trade_date=tick.session.trade_date,
                reference_cash_per_share=action.reference_cash_per_share,
                reference_shares_per_share=action.reference_shares_per_share, reference_price=action.reference_price):
                state.corporate_action_records.append(CorporateActionRecord(action.action_id, tick.ts_us,
                    action.symbol, "reference_updated", entitlement.eligible_qty if entitlement is not None else 0,
                    Decimal(0), Decimal(0), 0, action.source_ref))
        return context
