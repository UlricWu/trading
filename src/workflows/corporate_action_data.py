# filepath: src/workflows/corporate_action_data.py
"""Compose public announcement collection, local review extraction and V1 publication."""

from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from src.access import Access
from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.jobs.requests import TrainingDateRange
from src.observability.instrumentation import Instrumentation
from src.utils.path import PathManager


def run_corporate_action_preparation(
    *, path_manager: PathManager, registration_interval: TrainingDateRange,
    announcement_interval: TrainingDateRange, review_refs: Sequence[Path] = (),
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
            announcement_end=date.fromisoformat(announcement_interval.end), clock=lambda: datetime.now(timezone.utc)),
        ExtractDistributionTermsStep(pm=path_manager, review_refs=tuple(review_refs)),
        ResolveCorporateActionsStep(pm=path_manager),
    ), instrumentation=Instrumentation(f"corporate-action-data_{registration_interval.start}_{registration_interval.end}"),
    ).run(DataContext(start=registration_interval.start, end=registration_interval.end, trade_dates=dates))
