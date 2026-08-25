# filepath: src/data_system/steps/calendar_materialize.py
"""Materialize formal trade calendars and resolve requested open dates."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import cast

from src import logs
from src.access import Access, meta
from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.brokers.tushare import TushareBroker
from src.data_system.context import DataContext
from src.data_system.normalize.tushare import normalize_tushare
from src.utils.datetime_utils import DateTimeUtils
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


class CalendarMaterializeStep:
    """Materialize annual calendars and append formal trade dates to Context.

    Example:
        step = CalendarMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            access=access,
            processed_version="v1",
            adapter_cache={},
        )
        context = step.run(
            DataContext(start="2026-07-01", end="2026-07-20")
        )
    """

    def __init__(
        self,
        *,
        app_config: AppConfig,
        path_manager: PathManager,
        access: Access,
        processed_version: str,
        adapter_cache: MutableMapping[str, BrokerAdapter],
    ) -> None:
        """Bind calendar I/O and the workflow-owned broker cache.

        Example:
            step = CalendarMaterializeStep(
                app_config=app_config,
                path_manager=path_manager,
                access=access,
                processed_version="v1",
                adapter_cache={},
            )
        """
        self._app_config = app_config
        self._path_manager = path_manager
        self._access = access
        self._processed_version = processed_version
        self._adapter_cache = adapter_cache

    def run(self, context: DataContext) -> DataContext:
        """Materialize intersecting years and resolve requested trade dates.

        Example:
            context = step.run(
                DataContext(start="2025-12-20", end="2026-01-10")
            )
        """
        start_date = DateTimeUtils.require_system_date(
            context.start,
            field_name="start",
        )
        end_date = DateTimeUtils.require_system_date(
            context.end,
            field_name="end",
        )
        if start_date > end_date:
            raise ValueError(f"invalid date range: start={start_date}, end={end_date}")

        calendar_years = range(int(start_date[:4]), int(end_date[:4]) + 1)
        published_years = 0
        for calendar_year in calendar_years:
            if self._materialize_year(calendar_year):
                published_years += 1

        context.trade_dates = tuple(
            self._access.trade_dates(
                start_date=start_date,
                end_date=end_date,
            )
        )
        logs.info(
            f"✅ calendar materialize; years={len(calendar_years)} "
            f"reused={len(calendar_years) - published_years} "
            f"published={published_years} trade_dates={len(context.trade_dates)}"
        )
        return context

    def _materialize_year(self, calendar_year: int) -> bool:
        processed_payload = self._path_manager.processed_year_data(
            dataset_name="trade_calendar",
            version=self._processed_version,
            calendar_year=calendar_year,
        )
        processed_meta = self._path_manager.processed_year_meta(
            dataset_name="trade_calendar",
            version=self._processed_version,
            calendar_year=calendar_year,
        )
        if (
            meta.find(
                pm=self._path_manager,
                meta_path=processed_meta,
                expected_payload_path=processed_payload,
            )
            is not None
        ):
            logs.info(
                f"♻️ calendar processed meta hit; "
                f"calendar_year={calendar_year} meta={processed_meta}"
            )
            return False

        raw_payload = self._path_manager.raw_year_payload(
            broker=TushareBroker.name,
            source_name="trade_calendar",
            calendar_year=calendar_year,
            payload_file="data.parquet",
        )
        raw_meta = self._path_manager.raw_year_meta(
            broker=TushareBroker.name,
            source_name="trade_calendar",
            calendar_year=calendar_year,
        )
        loaded_raw = meta.find(
            pm=self._path_manager,
            meta_path=raw_meta,
            expected_payload_path=raw_payload,
        )
        if loaded_raw is None:
            adapter = self._adapter_cache.get(TushareBroker.name)
            if adapter is None:
                adapter = TushareBroker(app_cfg=self._app_config)
                self._adapter_cache[TushareBroker.name] = adapter
            broker = cast(TushareBroker, adapter)
            fetched_payload = broker.fetch_trade_calendar(
                calendar_year=calendar_year,
                pm=self._path_manager,
            )
            if fetched_payload is None:
                raise RuntimeError(
                    f"trade_calendar is unavailable; calendar_year={calendar_year}"
                )
            meta.commit(pm=self._path_manager, payload_path=fetched_payload)
            raw_payload = fetched_payload
        else:
            raw_payload = loaded_raw.payload_path
            logs.info(
                f"♻️ calendar raw meta hit; calendar_year={calendar_year} "
                f"meta={raw_meta}"
            )

        normalized = normalize_tushare(
            input_file=raw_payload,
            output_name=processed_payload,
            raw_object="trade_calendar",
            target_name="trade_calendar",
        )
        write_parquet_atomic(
            output_file=processed_payload,
            table=normalized.table,
        )
        meta.commit(
            pm=self._path_manager,
            payload_path=processed_payload,
            upstream_meta_path=raw_meta,
        )
        logs.info(
            f"✅ calendar publish; calendar_year={calendar_year} "
            f"output={processed_payload}"
        )
        return True
