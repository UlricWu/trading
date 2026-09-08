# filepath: src/data_system/steps/calendar_materialize.py
"""Materialize formal trade calendars and resolve requested open dates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from src import logs
from src.access import Access, meta
from src.data_system.brokers.tushare import TushareBroker
from src.data_system.context import DataContext
from src.data_system.normalize.tushare import normalize_tushare
from src.data_system.steps._partition import _publish_partition
from src.utils.path import PathManager


class CalendarMaterializeStep:
    """Materialize annual calendars and append formal trade dates to Context.

    Example:
        step = CalendarMaterializeStep(
            path_manager=path_manager,
            get_broker=get_broker,
            access=access,
            processed_version="v1",
        )
        context = step.run(
            DataContext(start="2026-07-01", end="2026-07-20")
        )
    """

    def __init__(
        self,
        *,
        path_manager: PathManager,
        get_broker: Callable[[], TushareBroker],
        access: Access,
        processed_version: str,
    ) -> None:
        """Bind calendar I/O and lazy access to the workflow's Tushare broker.

        Example:
            step = CalendarMaterializeStep(
                path_manager=path_manager,
                get_broker=get_broker,
                access=access,
                processed_version="v1",
            )
        """
        self._get_broker = get_broker
        self._path_manager = path_manager
        self._access = access
        self._processed_version = processed_version

    def run(self, context: DataContext) -> DataContext:
        """Materialize a validated range and resolve its formal trade dates.

        Example:
            context = step.run(
                DataContext(start="2025-12-20", end="2026-01-10")
            )
        """
        calendar_years = range(int(context.start[:4]), int(context.end[:4]) + 1)
        for calendar_year in calendar_years:
            self._materialize_year(calendar_year)

        context.trade_dates = tuple(
            self._access.trade_dates(
                start_date=context.start,
                end_date=context.end,
            )
        )
        logs.info(
            f"✅ calendar materialize; years={len(calendar_years)} "
            f"trade_dates={len(context.trade_dates)}"
        )
        return context

    def _materialize_year(self, calendar_year: int) -> None:
        processed_paths = self._path_manager.processed_year_object(
            dataset_name="trade_calendar",
            version=self._processed_version,
            calendar_year=calendar_year,
        )
        raw_meta_path = self._path_manager.raw_year_meta(
            broker=TushareBroker.name,
            source_name="trade_calendar",
            calendar_year=calendar_year,
        )

        who = (
            f"calendar; calendar_year={calendar_year} "
            f"output={processed_paths.payload_path}"
        )
        rows = _publish_partition(
            pm=self._path_manager,
            paths=processed_paths,
            who=who,
            upstream_meta_path=raw_meta_path,
            build=lambda: (
                normalize_tushare(
                    input_file=self._ensure_raw_calendar(
                        calendar_year=calendar_year,
                        raw_meta_path=raw_meta_path,
                    ),
                    output_name=processed_paths.payload_path,
                    raw_object="trade_calendar",
                    target_name="trade_calendar",
                ).table
            ),
        )
        if rows is None:
            logs.info(f"♻️ {who}")
        else:
            logs.info(f"✅ {who} rows={rows}")

    def _ensure_raw_calendar(self, *, calendar_year: int, raw_meta_path: Path) -> Path:
        pm = self._path_manager
        existing_raw = meta.find(
            pm=pm,
            meta_path=raw_meta_path,
            expected_payload_path=pm.raw_year_payload(
                broker=TushareBroker.name,
                source_name="trade_calendar",
                calendar_year=calendar_year,
                payload_file="data.parquet",
            ),
        )
        if existing_raw is not None:
            logs.info(
                f"♻️ calendar raw meta hit; calendar_year={calendar_year} "
                f"meta={raw_meta_path}"
            )
            return existing_raw.payload_path

        payload_path = self._get_broker().fetch_trade_calendar(
            calendar_year=calendar_year,
            pm=pm,
        )
        if payload_path is None:
            raise RuntimeError(
                f"trade_calendar is unavailable; calendar_year={calendar_year}"
            )
        meta.commit(pm=pm, payload_path=payload_path)
        return payload_path
