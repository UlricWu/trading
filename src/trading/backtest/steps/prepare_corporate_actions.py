# filepath: src/trading/backtest/steps/prepare_corporate_actions.py
"""Compile resolved distributions and their dated revisions into a replay schedule."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from hashlib import sha256

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.data_system.corporate_actions import CorporateActionResolution
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
        digests: dict[str, str] = {}
        sessions = {session.trade_date: session for session in context.sessions}
        initial_actions = {item.distribution.action_id: item.distribution
                           for item in self.initial_account.distribution_entitlements}
        initial_record_dates = {DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(action.record_ts_us)).date().isoformat()
                                for action in initial_actions.values()}
        for day in sorted(set(sessions) | initial_record_dates):
            record_ts_us = _timestamp(date.fromisoformat(day), time(15))
            source_ref = f"corporate_actions/v1/{day}"
            prepared = self.access.corporate_actions(record_date=day)
            if prepared is None:
                issues.append(CorporateActionIssue(ts_us=record_ts_us,
                    reason="missing_distribution_data", source_ref=source_ref))
            else:
                digests[source_ref] = sha256("\n".join(item.model_dump_json() for item in prepared).encode()).hexdigest()
                for item in prepared:
                    if day not in sessions:
                        if item.review is None or item.review.action_id not in initial_actions:
                            continue
                    elif item.symbol not in context.symbols_by_date[day]:
                        continue
                    resolutions.append(item)
                    if item.review is None:
                        issues.append(CorporateActionIssue(ts_us=record_ts_us, symbol=item.symbol,
                            action_id=item.candidate_id, reason=item.reason, source_ref=source_ref))
                        continue
                    review = item.review
                    if review.action_id in actions:
                        if actions[review.action_id].record_ts_us != record_ts_us:
                            raise ValueError(f"distribution action spans registration dates: {review.action_id}")
                        continue
                    for event_date in (review.ex_date, review.pay_date, review.div_listdate):
                        if (event_date is not None and context.sessions[0].trade_date <= event_date.isoformat()
                            <= context.sessions[-1].trade_date and event_date.isoformat() not in sessions):
                            raise ValueError(f"distribution event is not a prepared trading day: {review.action_id}/{event_date}")
                    for terms in review.terms:
                        action = Distribution(action_id=review.action_id, symbol=item.symbol, source_ref=source_ref,
                            announced_ts_us=_timestamp(terms.published_date + timedelta(days=1), time()),
                            record_ts_us=record_ts_us, ex_ts_us=_timestamp(review.ex_date, time()),
                            pay_ts_us=_timestamp(review.pay_date, time(15)) if review.pay_date is not None else None,
                            shares_available_ts_us=_timestamp(review.div_listdate, time(9, 30)) if review.div_listdate is not None else None,
                            cash_per_share=terms.cash_per_share, shares_per_share=terms.shares_per_share,
                            reference_cash_per_share=terms.reference_cash_per_share,
                            reference_shares_per_share=terms.reference_shares_per_share)
                        if review.action_id in actions:
                            revisions.append(action)
                        else:
                            actions[review.action_id] = action
        for session in context.sessions:
            day = session.trade_date
            notice_frame = self.access.corporate_action_notices(trade_date=day)
            if notice_frame is not None:
                ref = f"corporate_action_notices/v1/{day}"
                digests[ref] = sha256(notice_frame.to_json(orient="records").encode()).hexdigest()
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
        context.corporate_actions = CorporateActionSchedule(tuple(actions.values()), tuple(notices), digests,
            tuple(issues), tuple(revisions), tuple(resolutions))
        return context


def _timestamp(day: date, local_time: time) -> int:
    """Convert a source date to the workflow's explicitly declared account time."""
    return DateTimeUtils.local_time_to_utc_epoch_us(local_time, day)
