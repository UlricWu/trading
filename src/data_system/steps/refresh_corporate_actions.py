# filepath: src/data_system/steps/refresh_corporate_actions.py
"""Refresh affected corporate-action evidence inside standard data production."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from src.data_system.announcement_maintenance import prepare_announcement_updates
from src.data_system.context import DataContext
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.data_system.steps.collect_bse_announcements import CollectBseAnnouncementsStep
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class RefreshCorporateActionsStep:
    """Refresh known registration partitions and consume explicitly supplied corrections.

    Early announcements are already retained by MaterializeAnnouncementFactsStep.
    This step only projects evidence into the registration-based accounting dataset.

    Example:
        step = RefreshCorporateActionsStep(pm=pm, clock=utc_now)
        context = step.run(context)
    """

    pm: PathManager
    clock: Callable[[], datetime]
    review_refs: tuple[Path, ...] = ()
    identity_refs: tuple[Path, ...] = ()

    def run(self, context: DataContext) -> DataContext:
        """Refresh original registrations, retaining the caller's requested dates.

        Example:
            context = step.run(DataContext(start="2026-09-19", end="2026-09-20"))
        """
        updates = prepare_announcement_updates(path_manager=self.pm,
            start=date.fromisoformat(context.start), end=date.fromisoformat(context.end),
            identity_refs=self.identity_refs, review_refs=self.review_refs)
        dates = set(context.trade_dates)
        for refresh in updates.refreshes:
            target = DataContext(start=refresh.registration_date, end=refresh.registration_date,
                trade_dates=(refresh.registration_date,))
            step = (CollectAnnouncementsStep(pm=self.pm, announcement_start=refresh.announcement_start,
                announcement_end=refresh.announcement_end, clock=self.clock, identity_refs=self.identity_refs,
                observed_publications=refresh.publications, review_refs=self.review_refs)
                if refresh.provider == "cninfo" else CollectBseAnnouncementsStep(pm=self.pm,
                    announcement_start=refresh.announcement_start, announcement_end=refresh.announcement_end,
                    clock=self.clock, observed_publications=refresh.publications, review_refs=self.review_refs))
            step.run(target)
            dates.add(refresh.registration_date)
        target = DataContext(start=context.start, end=context.end, trade_dates=tuple(sorted(dates)))
        ExtractDistributionTermsStep(pm=self.pm, review_refs=self.review_refs).run(target)
        ResolveCorporateActionsStep(pm=self.pm).run(target)
        return context
