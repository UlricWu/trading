# filepath: tests/trading/backtest/steps/test_prepare_corporate_actions.py
"""Preparation consumes dispositions and keeps post-registration revisions separate."""
import json
from pathlib import Path
from unittest.mock import Mock

from pydantic import TypeAdapter
import pytest

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, resolve_corporate_actions
from src.data_system.announcement_publications import AnnouncementPublication
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_corporate_actions import PrepareCorporateActionsStep
from src.trading.backtest.steps.prepare_replay_sessions import PrepareReplaySessionsStep
from src.trading.portfolio.corporate_actions import DistributionEntitlement


@pytest.mark.parametrize("independent", [False, True])
def test_prepare_keeps_original_terms_and_later_revision(independent: bool) -> None:
    cases = json.loads((Path(__file__).parents[3] / "fixtures/corporate_actions/verified_distributions.json").read_text())
    case = next(item for item in cases if item["records"][0]["symbol"] == "003001")
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    if independent:
        original_url, original_digest = review.evidence_refs[0].split("#sha256=")
        review = CorporateActionReview.model_validate({**review.model_dump(),
            "candidate_id": "cninfo:123", "action_id": "cninfo:123:ordinary_shareholder",
            "source_digest": original_digest,
            "related_sources": {review.candidate_id: review.source_digest},
            "announcement_source": AnnouncementPublication(source="cninfo", symbol="003001", announcement_id="123",
                title="权益分派实施公告", published_date=review.terms[0].published_date, url=original_url)})
    prepared = resolve_corporate_actions(records, (review,))
    access = Mock(spec=Access)
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
    assert [issue.reason for issue in context.corporate_actions.issues] == ["different_announcement_dates"]
    access.corporate_actions.side_effect = None
    access.corporate_actions.return_value = None
    step.run(context)
    assert [issue.reason for issue in context.corporate_actions.issues] == ["missing_distribution_data"] * 2
    access.corporate_actions.return_value = ()
    step.run(context)
    assert context.corporate_actions.issues == ()
