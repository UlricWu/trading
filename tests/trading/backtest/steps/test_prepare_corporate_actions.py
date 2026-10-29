# filepath: tests/trading/backtest/steps/test_prepare_corporate_actions.py
"""Preparation consumes dispositions and keeps post-registration revisions separate."""
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from tests.fixtures.distributions import verified_distributions
from unittest.mock import Mock

from pydantic import TypeAdapter
import pytest

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, resolve_corporate_actions
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.distribution_announcements import AnnouncementText, extract_distribution_review
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_corporate_actions import PrepareCorporateActionsStep
from src.trading.backtest.steps.prepare_replay_sessions import PrepareReplaySessionsStep
from src.trading.portfolio.corporate_actions import DistributionEntitlement
from src.utils.datetime_utils import DateTimeUtils


@pytest.mark.parametrize("case_index,days,available", [
    (0, ["2023-12-25", "2023-12-26", "2023-12-27", "2023-12-28"], "2023-12-28T00:00:00+08:00"),
    (1, ["2025-09-10", "2025-09-11", "2025-09-12", "2025-09-15"], "2025-09-13T00:00:00+08:00"),
])
def test_original_zero_rights_keep_later_reference_in_future(case_index: int, days: Sequence[str], available: str) -> None:
    case = json.loads((Path(__file__).parents[3] / "fixtures/corporate_actions/body_matched_events.json").read_text())[case_index]
    rows = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    docs = TypeAdapter(tuple[AnnouncementText, ...]).validate_python(case["documents"])
    extraction = extract_distribution_review(rows, docs)
    assert extraction.review is not None, extraction.reason
    resolutions = resolve_corporate_actions(rows, (extraction.review,))
    access = Mock(spec=Access)
    access.announcement_history.return_value = ()
    symbol = rows[0].symbol
    access.trade_dates.return_value = days
    access.corporate_actions.side_effect = lambda record_date: resolutions if record_date == days[1] else ()
    access.corporate_action_notices.return_value = None
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        symbols_by_date={day: (symbol,) for day in days})
    PrepareReplaySessionsStep(access, days[0], days[-1]).run(context)
    initial = InitialAccount(ts_us=context.sessions[0].start_ts_us, trade_date=days[0], cash=0,
        positions={symbol: 100}, sellable={symbol: 100}, average_cost={symbol: "2.68"}, peak_equity=268)
    PrepareCorporateActionsStep(access, initial).run(context)
    schedule = context.corporate_actions
    original, = schedule.distributions
    final, = schedule.revisions
    assert schedule.registrations == schedule.payments == schedule.releases == {}
    assert original.cash_per_share == original.shares_per_share == 0
    assert original.reference_price is original.reference_cash_per_share is original.reference_shares_per_share is None
    if case_index == 0:
        assert final.reference_price == Decimal("2.52")
    else:
        assert final.reference_price is None
        assert final.reference_cash_per_share == final.reference_shares_per_share == 0
    assert final.announced_ts_us > original.ex_ts_us
    assert DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(final.announced_ts_us)).isoformat() == available
    prefix = extract_distribution_review(rows, tuple(doc for doc in docs if doc.published_date < date.fromisoformat(days[2])))
    assert prefix.review is not None
    access.corporate_actions.side_effect = lambda record_date: resolve_corporate_actions(rows, (prefix.review,)) if record_date == days[1] else ()
    PrepareCorporateActionsStep(access, initial).run(context)
    assert context.corporate_actions.distributions == (original,)
    assert context.corporate_actions.revisions == ()


def test_qualified_reorganization_prepares_pending_rights_without_future_reference() -> None:
    case = json.loads((Path(__file__).parents[3] / "fixtures/corporate_actions/qualified_reorganization.json").read_text())
    rows = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    resolutions = resolve_corporate_actions(rows, (review,))
    access = Mock(spec=Access)
    access.announcement_history.return_value = ()
    days = ["2021-12-29", "2021-12-30", "2021-12-31"]
    access.trade_dates.return_value = days
    access.corporate_actions.side_effect = lambda record_date: resolutions if record_date == days[1] else ()
    access.corporate_action_notices.return_value = None
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        symbols_by_date={day: ("000564",) for day in days})
    PrepareReplaySessionsStep(access, days[0], days[-1]).run(context)
    initial = InitialAccount(ts_us=context.sessions[0].start_ts_us, trade_date=days[0], cash=0,
        positions={}, sellable={}, average_cost={}, peak_equity=0)
    PrepareCorporateActionsStep(access, initial).run(context)
    original, = context.corporate_actions.distributions
    final, = context.corporate_actions.revisions
    assert original.shares_per_share == final.shares_per_share == Decimal("2.1898786")
    assert original.reference_price is original.reference_cash_per_share is original.reference_shares_per_share is None
    assert final.reference_price == Decimal("1.80")
    assert original.announced_ts_us < original.record_ts_us < final.announced_ts_us == final.ex_ts_us
    assert original.record_ts_us == context.sessions[1].close_ts_us
    snapshot = InitialAccount(ts_us=original.record_ts_us, trade_date=days[1], cash=0,
        positions={"000564": 5_000_000}, sellable={"000564": 5_000_000}, average_cost={"000564": "4.28"},
        peak_equity=21_400_000,
        distribution_entitlements=(DistributionEntitlement(distribution=original, eligible_qty=5_000_000),))
    resumed = BacktestContext(metrics=context.metrics, sessions=context.sessions[-1:], symbols_by_date={days[-1]: ("000564",)})
    PrepareCorporateActionsStep(access, snapshot).run(resumed)
    assert resumed.corporate_actions.distributions == (original,)
    assert resumed.corporate_actions.revisions == (final,)


@pytest.mark.parametrize("null_cash", [False, True])
def test_prepare_and_resume_compile_ordinary_source_without_requiring_a_review(null_cash: bool) -> None:
    row = DividendRecord.model_validate_json(
        (Path(__file__).parents[3] / "fixtures/corporate_actions/ordinary_dividend.json").read_bytes())
    if null_cash:
        row = row.model_copy(update={"cash_div_tax": None, "pay_date": None})
    earlier = row.model_copy(update={"imp_ann_date": date(2026, 7, 15)})
    prepared = resolve_corporate_actions((earlier, row, row), ())
    access = Mock(spec=Access)
    access.announcement_history.return_value = ()
    days = ["2026-07-17", "2026-07-20", "2026-07-21"]
    access.trade_dates.return_value = days
    access.corporate_actions.side_effect = lambda record_date: prepared if record_date == days[0] else ()
    access.corporate_action_notices.return_value = None
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        symbols_by_date={day: (row.symbol,) for day in days})
    PrepareReplaySessionsStep(access, days[0], days[-1]).run(context)
    initial = InitialAccount(ts_us=context.sessions[0].start_ts_us, trade_date=days[0], cash=0,
        positions={}, sellable={}, average_cost={}, peak_equity=0)

    PrepareCorporateActionsStep(access, initial).run(context)

    action, = context.corporate_actions.distributions
    assert action.action_id == prepared[0].action_id
    assert action.cash_per_share == (Decimal(0) if null_cash else row.cash_div_tax)
    assert action.shares_per_share == row.stk_div
    assert action.reference_basis == "ordinary_formula_assumption"
    assert action.reference_cash_per_share == action.cash_per_share
    assert action.reference_shares_per_share == row.stk_div
    assert action.announced_ts_us == context.sessions[0].start_ts_us
    assert action.announced_ts_us < action.record_ts_us < action.ex_ts_us
    assert action.ex_ts_us == context.sessions[1].start_ts_us
    assert action.pay_ts_us == (None if null_cash else context.sessions[-1].close_ts_us)
    assert context.corporate_actions.revisions == ()
    assert context.corporate_actions.issues == ()
    assert context.corporate_actions.resolutions == prepared
    snapshot = InitialAccount(ts_us=action.record_ts_us, trade_date=days[0], cash=0,
        positions={}, sellable={}, average_cost={}, peak_equity=0,
        distribution_entitlements=(DistributionEntitlement(distribution=action, eligible_qty=200),))
    resumed = BacktestContext(metrics=context.metrics, sessions=context.sessions[1:],
        symbols_by_date={day: (row.symbol,) for day in days[1:]})
    PrepareCorporateActionsStep(access, snapshot).run(resumed)
    assert resumed.corporate_actions.distributions == (action,)
    assert resumed.corporate_actions.resolutions == prepared
    correction = CorporateActionReview(candidate_id=row.candidate_id,
        action_id="cninfo:confirmed:ordinary_shareholder", shareholder_scope="ordinary_shareholder",
        record_date=row.record_date, ex_date=row.ex_date, pay_date=date(2026, 7, 21), div_listdate=row.div_listdate,
        terms=[dict(published_date="2026-07-16", cash_per_share="1", shares_per_share="0.5",
            reference_cash_per_share="0.9", reference_shares_per_share="0.5")],
        evidence_refs=("fixture://confirmed",), reason="confirmed_reference_correction")
    corrected = resolve_corporate_actions((row,), (correction,))
    access.corporate_actions.side_effect = lambda record_date: corrected if record_date == days[0] else ()
    with pytest.raises(ValueError, match="initial entitlement source changed"):
        PrepareCorporateActionsStep(access, snapshot).run(resumed)
    PrepareCorporateActionsStep(access, initial).run(context)
    confirmed, = context.corporate_actions.distributions
    assert confirmed.reference_basis == "announcement"
    assert str(confirmed.reference_cash_per_share) == "0.9"


@pytest.mark.parametrize("independent", [False, True])
def test_prepare_keeps_original_terms_and_later_revision(independent: bool) -> None:
    cases = verified_distributions()
    case = next(item for item in cases if item["records"][0]["symbol"] == "003001")
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    if independent:
        original_url = review.evidence_refs[0]
        review = CorporateActionReview.model_validate({**review.model_dump(),
            "candidate_id": "cninfo:123", "action_id": "cninfo:123:ordinary_shareholder",
            "related_sources": (review.candidate_id,),
            "announcement_source": AnnouncementPublication(source="cninfo", symbol="003001", announcement_id="123",
                title="权益分派实施公告", published_date=review.terms[0].published_date, url=original_url)})
    prepared = resolve_corporate_actions(records, (review,))
    access = Mock(spec=Access)
    access.announcement_history.return_value = ()
    access.trade_dates.return_value = ["2025-07-04", "2025-07-07"]
    access.corporate_actions.side_effect = lambda record_date: prepared if record_date == "2025-07-04" else ()
    access.corporate_action_notices.return_value = None
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        symbols_by_date={day: ("003001",) for day in access.trade_dates.return_value})
    PrepareReplaySessionsStep(access, "2025-07-04", "2025-07-07").run(context)
    initial = InitialAccount(ts_us=context.sessions[0].start_ts_us, trade_date="2025-07-04", cash=0,
        positions={}, sellable={}, average_cost={}, peak_equity=0)
    step = PrepareCorporateActionsStep(access, initial)
    step.run(context)
    original, = context.corporate_actions.distributions
    revised, = context.corporate_actions.revisions
    assert original.action_id == revised.action_id
    assert original.reference_basis == revised.reference_basis == "announcement"
    assert str(original.cash_per_share) == "0.2816137"
    assert str(revised.cash_per_share) == "0.2814904"
    assert original.announced_ts_us < original.record_ts_us < revised.announced_ts_us < revised.ex_ts_us
    assert context.sessions[1].start_ts_us in context.corporate_actions.timestamps(context.sessions)
    resumed = BacktestContext(metrics=context.metrics, sessions=(context.sessions[1],),
        symbols_by_date={"2025-07-07": ("003001",)})
    snapshot = InitialAccount(ts_us=original.record_ts_us, trade_date="2025-07-04", cash=0,
        positions={}, sellable={}, average_cost={}, peak_equity=0,
        distribution_entitlements=(DistributionEntitlement(distribution=original, eligible_qty=10_000_000),))
    PrepareCorporateActionsStep(access, snapshot).run(resumed)
    assert resumed.corporate_actions.distributions == (original,)
    assert resumed.corporate_actions.revisions == (revised,)
    assert len(resumed.corporate_actions.resolutions) == (2 if independent else 1)
    access.corporate_actions.assert_any_call(record_date="2025-07-04")
    access.corporate_actions.side_effect = lambda record_date: resolve_corporate_actions(records, ()) if record_date == "2025-07-04" else ()
    step.run(context)
    assert context.corporate_actions.distributions == ()
    assert [issue.reason for issue in context.corporate_actions.issues] == ["unsupported_source_timeline"]
    access.corporate_actions.side_effect = None
    access.corporate_actions.return_value = None
    step.run(context)
    assert [issue.reason for issue in context.corporate_actions.issues] == ["missing_distribution_data"] * 2
    access.corporate_actions.return_value = ()
    step.run(context)
    assert context.corporate_actions.issues == ()
