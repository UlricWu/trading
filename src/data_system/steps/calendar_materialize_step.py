# filepath: src/data_system/steps/calendar_materialize_step.py
"""Materialize the requested natural-date calendar and resolve open dates."""

from __future__ import annotations

from src.access import Access
from src.data_system.context import DataContext
from src.data_system.source_materializer import SourceMaterializer
from src.utils.datetime_utils import DateTimeUtils


class CalendarMaterializeStep:
    """Materialize every requested calendar date in one Pipeline Step.

    Example:
        step = CalendarMaterializeStep(
            materializer=calendar_materializer,
            access=access,
        )
        context = step.run(
            DataContext(start="2026-07-01", end="2026-07-20")
        )
    """

    def __init__(
        self,
        *,
        materializer: SourceMaterializer,
        access: Access,
    ) -> None:
        """Bind the calendar producer and formal access boundary.

        Example:
            step = CalendarMaterializeStep(
                materializer=calendar_materializer,
                access=access,
            )
        """
        self._materializer = materializer
        self._access = access

    def run(self, context: DataContext) -> DataContext:
        """Materialize the complete calendar range and resolve trade dates.

        Example:
            context = step.run(
                DataContext(start="2026-07-01", end="2026-07-20")
            )
        """
        for natural_date in DateTimeUtils.date_range(context.start, context.end):
            if not self._materializer.materialize(natural_date):
                raise RuntimeError(
                    f"[OfflineData] missing trade_calendar trade_date={natural_date}"
                )
        context.trade_dates = tuple(
            self._access.trade_dates(
                start_date=context.start,
                end_date=context.end,
            )
        )
        return context
