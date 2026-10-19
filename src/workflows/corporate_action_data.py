# filepath: src/workflows/corporate_action_data.py
"""Compose public announcement collection, local review extraction and V1 publication."""

from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from src.access import Access
from src.data_system.announcement_maintenance import prepare_announcement_updates, summarize_announcement_maintenance
from src.data_system.announcement_publications import CorporateActionMaintenance
from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.data_system.steps.collect_bse_announcements import CollectBseAnnouncementsStep
from src.data_system.steps.collect_publications import CollectPublicationsStep
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.jobs.requests import TrainingDateRange
from src.observability.instrumentation import Instrumentation
from src.utils.path import PathManager


def run_corporate_action_preparation(
    *, path_manager: PathManager, registration_interval: TrainingDateRange,
    announcement_interval: TrainingDateRange, review_refs: Sequence[Path] = (),
    identity_refs: Sequence[Path] = (),
) -> None:
    """Prepare registered candidates using an explicit natural-day announcement window.

    Existing dividend/v1 and calendar inputs must already be committed. Public queries
    are read-only; all outputs use the supplied storage root.

    Example:
        run_corporate_action_preparation(path_manager=pm,
            registration_interval=TrainingDateRange(start="2026-09-01", end="2026-09-30"),
            announcement_interval=TrainingDateRange(start="2026-08-20", end="2026-10-17"),
            review_refs=(Path("/inputs/reviewed-distributions.json"),))
    """
    access = Access(pm=path_manager, processed_version="v1")
    dates = tuple(access.trade_dates(start_date=registration_interval.start, end_date=registration_interval.end))
    if not dates:
        raise ValueError("corporate-action preparation requires registration sessions")
    DataPipeline(steps=(
        CollectAnnouncementsStep(pm=path_manager, announcement_start=date.fromisoformat(announcement_interval.start),
            announcement_end=date.fromisoformat(announcement_interval.end), clock=lambda: datetime.now(timezone.utc),
            identity_refs=tuple(identity_refs), review_refs=tuple(review_refs)),
        CollectBseAnnouncementsStep(pm=path_manager, announcement_start=date.fromisoformat(announcement_interval.start),
            announcement_end=date.fromisoformat(announcement_interval.end), clock=lambda: datetime.now(timezone.utc),
            review_refs=tuple(review_refs)),
        ExtractDistributionTermsStep(pm=path_manager, review_refs=tuple(review_refs)),
        ResolveCorporateActionsStep(pm=path_manager),
    ), instrumentation=Instrumentation(f"corporate-action-data_{registration_interval.start}_{registration_interval.end}"),
    ).run(DataContext(start=registration_interval.start, end=registration_interval.end, trade_dates=dates))


def run_corporate_action_maintenance(
    *, path_manager: PathManager, publication_interval: TrainingDateRange,
    review_refs: Sequence[Path] = (), identity_refs: Sequence[Path] = (),
) -> CorporateActionMaintenance:
    """Discover publication changes, refresh affected evidence and rebuild original registration partitions.

    Queries are read-only and all writes stay under the supplied storage root. Missing
    source candidates are reported separately and are never created from assumed amounts.

    Example:
        result = run_corporate_action_maintenance(path_manager=pm,
            publication_interval=TrainingDateRange(start="2026-09-19", end="2026-09-20"),
            review_refs=(Path("/inputs/reviewed-corrections.json"),))
    """
    context = DataContext(start=publication_interval.start, end=publication_interval.end)
    CollectPublicationsStep(pm=path_manager, clock=lambda: datetime.now(timezone.utc)).run(context)
    updates = prepare_announcement_updates(path_manager=path_manager,
        start=date.fromisoformat(context.start), end=date.fromisoformat(context.end), identity_refs=identity_refs,
        review_refs=review_refs)
    for refresh in updates.refreshes:
        target = DataContext(start=refresh.registration_date, end=refresh.registration_date,
            trade_dates=(refresh.registration_date,))
        step = (CollectAnnouncementsStep(pm=path_manager, announcement_start=refresh.announcement_start,
            announcement_end=refresh.announcement_end, clock=lambda: datetime.now(timezone.utc),
            identity_refs=tuple(identity_refs), observed_publications=refresh.publications, review_refs=tuple(review_refs))
            if refresh.provider == "cninfo" else CollectBseAnnouncementsStep(pm=path_manager,
            announcement_start=refresh.announcement_start, announcement_end=refresh.announcement_end,
            clock=lambda: datetime.now(timezone.utc), observed_publications=refresh.publications, review_refs=tuple(review_refs)))
        step.run(target)
    dates = tuple(sorted({item.registration_date for item in updates.refreshes}))
    target = DataContext(start=dates[0] if dates else context.start, end=dates[-1] if dates else context.end, trade_dates=dates)
    DataPipeline(steps=(ExtractDistributionTermsStep(pm=path_manager, review_refs=tuple(review_refs)),
        ResolveCorporateActionsStep(pm=path_manager)), instrumentation=Instrumentation(
            f"corporate-action-maintenance_{context.start}_{context.end}")).run(target)
    return summarize_announcement_maintenance(path_manager=path_manager, updates=updates)
