# filepath: src/workflows/trade_calendar.py
"""Bootstrap every formal annual trade calendar from 2016 through an as-of year."""

from __future__ import annotations

from src import logs
from src.access import Access
from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.data_system.steps.calendar_materialize import CalendarMaterializeStep
from src.observability.instrumentation import Instrumentation
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION

_CALENDAR_BOOTSTRAP_START = "2016-01-01"


def run_trade_calendar_bootstrap(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    as_of_date: str,
) -> None:
    """Materialize complete calendar years from 2016 through the as-of year.

    Example:
        run_trade_calendar_bootstrap(
            app_config=app_config,
            path_manager=path_manager,
            as_of_date="2026-08-21",
        )
    """
    validated_as_of = DateTimeUtils.require_system_date(
        as_of_date,
        field_name="as_of_date",
    )
    end_date = f"{validated_as_of[:4]}-12-31"
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    adapter_cache: dict[str, BrokerAdapter] = {}
    pipeline = DataPipeline(
        steps=(
            CalendarMaterializeStep(
                app_config=app_config,
                path_manager=path_manager,
                access=access,
                processed_version=PROCESSED_VERSION,
                adapter_cache=adapter_cache,
            ),
        ),
        instrumentation=Instrumentation(
            f"data-calendar_{_CALENDAR_BOOTSTRAP_START}_{end_date}"
        ),
    )
    logs.info(
        f"started kind=data-calendar start={_CALENDAR_BOOTSTRAP_START} "
        f"end={end_date} as_of_date={validated_as_of}"
    )
    pipeline.run(
        DataContext(
            start=_CALENDAR_BOOTSTRAP_START,
            end=end_date,
        )
    )
    logs.info(
        f"finished kind=data-calendar start={_CALENDAR_BOOTSTRAP_START} "
        f"end={end_date} as_of_date={validated_as_of}"
    )


__all__ = ["run_trade_calendar_bootstrap"]
