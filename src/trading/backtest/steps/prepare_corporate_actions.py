# filepath: src/trading/backtest/steps/prepare_corporate_actions.py
"""Compile resolved distributions and their dated revisions into a replay schedule."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import cast

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.data_system.corporate_actions import CorporateActionResolution, DistributionTerms, DividendRecord
from src.data_system.announcement_facts import AnnouncementFacts, assemble_announcement_events
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.corporate_actions import CorporateActionIssue, CorporateActionNotice, CorporateActionSchedule
from src.trading.portfolio.corporate_actions import Distribution
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class PrepareCorporateActionsStep:
    """Read resolved local events; source disputes are resolved by the data workflow.

    Example:
        context = PrepareCorporateActionsStep(access, initial_account).run(context)
    """

    access: Access
    initial_account: InitialAccount

    def run(self, context: BacktestContext) -> BacktestContext:
        """Prepare fixed phase dates and retain versioned terms and source dispositions.

        Example:
            context = step.run(context)
        """
        actions: dict[str, Distribution] = {}
        revisions: list[Distribution] = []
        resolutions: list[CorporateActionResolution] = []
        notices: list[CorporateActionNotice] = []
        issues: list[CorporateActionIssue] = []
        sessions = {session.trade_date: session for session in context.sessions}
        initial_actions = {item.distribution.action_id: item.distribution
                           for item in self.initial_account.distribution_entitlements}
        initial_record_dates = {DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(action.record_ts_us)).date().isoformat()
                                for action in initial_actions.values()}
        for day in sorted(set(sessions) | initial_record_dates | self.initial_account.registration_holdings.keys()):
            record_ts_us = _timestamp(date.fromisoformat(day), time(15))
            source_ref = f"corporate_actions/v1/{day}"
            prepared = self.access.corporate_actions(record_date=day)
            if prepared is None:
                issues.append(CorporateActionIssue(ts_us=record_ts_us,
                    reason="missing_distribution_data", source_ref=source_ref))
            else:
                for item in prepared:
                    source_action_id = f"dividend:{item.candidate_id}:ordinary_shareholder"
                    if source_action_id in initial_actions and item.action_id != source_action_id:
                        raise ValueError(f"initial entitlement source changed; replay its registration: {item.candidate_id}")
                    if day not in sessions:
                        if item.action_id not in initial_actions and not (
                            day in self.initial_account.registration_holdings and item.review is not None
                            and item.review.kind == "compensation"):
                            continue
                    elif item.symbol not in context.symbols_by_date[day]:
                        continue
                    resolutions.append(item)
                    if item.status == "unresolved":
                        issues.append(CorporateActionIssue(ts_us=record_ts_us, symbol=item.symbol,
                            action_id=item.candidate_id, reason=item.reason, source_ref=source_ref))
                        continue
                    action_id = cast(str, item.action_id)
                    event = item.review if item.review is not None else cast(DividendRecord, item.source_record)
                    if action_id in actions:
                        if actions[action_id].record_ts_us != record_ts_us:
                            raise ValueError(f"distribution action spans registration dates: {action_id}")
                        continue
                    phase_dates = (event.ex_date, event.pay_date, event.div_listdate)
                    if item.review is not None and item.review.kind == "compensation":
                        phase_dates += tuple(day for terms in item.review.terms for day in
                            (terms.pay_date, terms.shares_credit_date, terms.shares_available_date))
                    for event_date in phase_dates:
                        if (event_date is not None and context.sessions[0].trade_date <= event_date.isoformat()
                            <= context.sessions[-1].trade_date and event_date.isoformat() not in sessions):
                            raise ValueError(f"distribution event is not a prepared trading day: {action_id}/{event_date}")
                    if item.review is not None:
                        versions = item.review.terms
                    else:
                        row = cast(DividendRecord, item.source_record)
                        cash = row.cash_div_tax if row.cash_div_tax is not None else Decimal(0)
                        versions = (DistributionTerms(published_date=cast(date, row.imp_ann_date),
                            cash_per_share=cash, shares_per_share=cast(Decimal, row.stk_div),
                            reference_cash_per_share=cash, reference_shares_per_share=cast(Decimal, row.stk_div)),)
                    for terms in versions:
                        compensation = item.review is not None and item.review.kind == "compensation"
                        pay_date = terms.pay_date if compensation else event.pay_date
                        available_date = terms.shares_available_date if compensation else event.div_listdate
                        action = Distribution(action_id=action_id, symbol=item.symbol, source_ref=source_ref,
                            announced_ts_us=terms.available_ts_us,
                            record_ts_us=record_ts_us,
                            ex_ts_us=_timestamp(event.ex_date, time()) if event.ex_date is not None else None,
                            kind="compensation" if compensation else "distribution", benefit_state=terms.benefit_state,
                            pay_ts_us=_timestamp(pay_date, time(15)) if pay_date is not None else None,
                            shares_available_ts_us=_timestamp(available_date, time(9, 30)) if available_date is not None else None,
                            shares_credit_ts_us=_timestamp(terms.shares_credit_date, time(15)) if terms.shares_credit_date is not None else None,
                            shares_numerator=terms.shares_numerator, shares_denominator=terms.shares_denominator,
                            cash_per_share=terms.cash_per_share, shares_per_share=terms.shares_per_share,
                            reference_cash_per_share=terms.reference_cash_per_share,
                            reference_shares_per_share=terms.reference_shares_per_share,
                            reference_price=terms.reference_price,
                            reference_basis="announcement" if item.review is not None else "ordinary_formula_assumption")
                        if action_id in actions:
                            revisions.append(action)
                        else:
                            actions[action_id] = action
        symbols = tuple(sorted({symbol for values in context.symbols_by_date.values() for symbol in values}
            | self.initial_account.positions.keys()))
        history = self.access.announcement_history(as_of_ts_us=context.sessions[-1].close_ts_us, symbols=symbols)
        by_event: dict[str, list[AnnouncementFacts]] = {}
        for row in history:
            by_event.setdefault(row.event_id, []).append(row)
        for event_rows in by_event.values():
            for available in sorted({row.available_ts_us for row in event_rows}):
                event, = assemble_announcement_events(tuple(row for row in event_rows if row.available_ts_us <= available))
                if event.record_date is None or event.benefit_state == "announced":
                    continue
                action_id = event.event_id + ":ordinary_shareholder"
                symbol = event.publication.symbol
                source_ref = f"announcement_facts/v1/{event.event_id}"
                represented = [item for item in resolutions if item.symbol == symbol and item.record_date == event.record_date]
                if represented:
                    # A second account event requires an explicit association decision; a shared
                    # registration alone cannot either merge or duplicate a settled distribution.
                    if event.kind in ("reorganization", "compensation") and not any(item.review is not None and (
                        item.action_id == action_id or set(item.review.evidence_refs) & set(event.evidence_refs))
                        for item in represented):
                        issues.append(CorporateActionIssue(ts_us=available, symbol=symbol, action_id=action_id,
                            source_ref=source_ref, reason="announcement_account_link_pending"))
                    continue
                if event.conflicts or event.parse_issue is not None:
                    issues.append(CorporateActionIssue(ts_us=available, symbol=symbol, action_id=action_id,
                        source_ref=source_ref, reason=event.parse_issue or "announcement_fact_conflict:" + ",".join(event.conflicts)))
                    continue
                if event.kind == "other":
                    continue
                record_ts_us = _timestamp(event.record_date, time(15))
                if action_id in actions and actions[action_id].record_ts_us != record_ts_us:
                    issues.append(CorporateActionIssue(ts_us=available, symbol=symbol, action_id=action_id,
                        source_ref=source_ref, reason="registration_correction_requires_review"))
                    continue
                numerator, denominator = event.shares_numerator, event.shares_denominator
                formula = numerator is not None and denominator is not None
                shares = (Decimal(numerator) / Decimal(denominator)
                    if formula and event.shares_per_share is None else event.shares_per_share)
                reference_cash, reference_shares = event.reference_cash_per_share, event.reference_shares_per_share
                if reference_cash is None or reference_shares is None:
                    reference_cash = reference_shares = None
                assumption = (event.kind == "distribution" and event.cash_per_share is not None and shares is not None
                    and reference_cash is None and reference_shares is None and event.reference_price is None)
                if assumption:
                    reference_cash, reference_shares = event.cash_per_share, shares
                action = Distribution(action_id=action_id, symbol=symbol, source_ref=source_ref,
                    announced_ts_us=available, record_ts_us=record_ts_us,
                    ex_ts_us=_timestamp(event.ex_date, time()) if event.ex_date is not None else None,
                    kind="compensation" if event.kind == "compensation" else "distribution",
                    benefit_state=event.benefit_state or "conditional",
                    cash_per_share=event.cash_per_share, shares_per_share=shares,
                    shares_numerator=numerator if formula else None, shares_denominator=denominator if formula else None,
                    pay_ts_us=_timestamp(event.pay_date, time(15)) if event.pay_date is not None else None,
                    shares_credit_ts_us=_timestamp(event.shares_credit_date, time(15))
                        if event.kind == "compensation" and event.shares_credit_date is not None else None,
                    shares_available_ts_us=_timestamp(event.shares_available_date, time(9, 30))
                        if event.shares_available_date is not None else None,
                    reference_cash_per_share=reference_cash, reference_shares_per_share=reference_shares,
                    reference_price=event.reference_price,
                    reference_basis="ordinary_formula_assumption" if assumption else "announcement")
                if action_id in actions:
                    revisions.append(action)
                else:
                    actions[action_id] = action
        for session in context.sessions:
            day = session.trade_date
            notice_frame = self.access.corporate_action_notices(trade_date=day)
            if notice_frame is not None:
                for row in notice_frame.to_dict(orient="records"):
                    notice = CorporateActionNotice.model_validate(row)
                    if not session.start_ts_us <= notice.effective_ts_us <= session.close_ts_us:
                        raise ValueError(f"notice effective time must belong to partition: {notice.action_id}/{day}")
                    if notice.symbol in context.symbols_by_date[day]:
                        notices.append(notice)
        if len({notice.action_id for notice in notices}) != len(notices):
            raise ValueError("corporate action notice identities must be unique")
        for item in self.initial_account.distribution_entitlements:
            action = item.distribution
            visible = [version for version in (*actions.values(), *revisions)
                       if version.action_id == action.action_id and version.announced_ts_us <= self.initial_account.ts_us]
            if visible and max(visible, key=lambda version: version.announced_ts_us) != action:
                raise ValueError(f"initial entitlement conflicts with visible distribution terms: {action.action_id}")
            if action.action_id not in actions:
                actions[action.action_id] = action
        context.corporate_actions = CorporateActionSchedule(tuple(actions.values()), tuple(notices),
            tuple(issues), tuple(revisions), tuple(resolutions))
        return context


def _timestamp(day: date, local_time: time) -> int:
    """Convert a source date to the workflow's explicitly declared account time."""
    return DateTimeUtils.local_time_to_utc_epoch_us(local_time, day)
