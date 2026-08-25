# filepath: src/data_system/brokers/tushare.py
"""Tushare broker implementation for configured raw-object ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Protocol

import pandas
import tushare as ts

from src import logs
from src.config.app_config import AppConfig
from src.data_system.brokers.base import DownloadPlan
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class _TushareClient(Protocol):
    _DataApi__http_url: str

    def query(self, api_name: str, **params: str) -> object: ...


class TushareBroker:
    """
    Materialize one Tushare raw object response directly into raw.

    The broker owns the active structured-source manifest and source-side no-data
    translation. Each manifest key is the shared source, raw-object, and processed
    output identity; each value is its Tushare API name. The broker does not
    normalize or commit metadata.

    Example:
        broker = TushareBroker(app_cfg=AppConfig.load())
    """

    name: ClassVar[str] = "tushare"

    _TUSHARE_SOURCES: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "trade_calendar": "trade_cal",
            "daily_bar": "daily",
            "adj_factor": "adj_factor",
            "daily_basic": "daily_basic",
            "stock_basic": "bak_basic",
            "stock_st": "stock_st",
            "stk_limit": "stk_limit",
            "suspend_d": "suspend_d",
            "cyq_perf": "cyq_perf",
            "margin": "margin",
            "margin_detail": "margin_detail",
            "moneyflow": "moneyflow",
            "top_list": "top_list",
        }
    )

    def __init__(self, *, app_cfg: AppConfig) -> None:
        """Initialize the Tushare client from validated AppConfig settings.

        Example:
            broker = TushareBroker(app_cfg=AppConfig.load())
        """
        ts.set_token(app_cfg.secret.tushare_token)
        self._pro: _TushareClient = ts.pro_api()
        if app_cfg.secret.tushare_gateway is not None:
            self._pro._DataApi__http_url = app_cfg.secret.tushare_gateway

    @classmethod
    def active_source_names(cls) -> tuple[str, ...]:
        """Return the formally active structured Tushare sources.

        Example:
            source_names = TushareBroker.active_source_names()
        """
        return tuple(cls._TUSHARE_SOURCES)

    def fetch(
        self,
        *,
        record: DownloadPlan,
        pm: PathManager,
    ) -> DownloadPlan | None:
        """Fetch one Tushare raw object as raw `data.parquet`.

        Example:
            fetched = broker.fetch(record=download_plan, pm=path_manager)
        """

        if record.raw_object not in self._TUSHARE_SOURCES:
            raise ValueError(
                f"TushareBroker does not support raw_object={record.raw_object!r} "
                f"for source_name={record.source_name!r}"
            )
        if record.raw_object == "trade_calendar":
            raise ValueError(
                "trade_calendar must be fetched with fetch_trade_calendar()"
            )

        api_name = self._TUSHARE_SOURCES[record.raw_object]
        compact_date = DateTimeUtils.to_compact_date(record.trade_date)
        raw_payload = pm.raw_payload(
            broker=record.broker,
            trade_date=record.trade_date,
            source_name=record.source_name,
            payload_file=record.payload_file,
        )
        if not self._materialize_query(
            source_name=record.source_name,
            api_name=api_name,
            params={"trade_date": compact_date},
            raw_payload=raw_payload,
        ):
            return None

        return DownloadPlan(
            trade_date=record.trade_date,
            broker=record.broker,
            source_name=record.source_name,
            raw_object=record.raw_object,
            payload_file=raw_payload.name,
        )

    def fetch_trade_calendar(
        self,
        *,
        calendar_year: int,
        pm: PathManager,
    ) -> Path | None:
        """Fetch one complete Tushare SSE calendar year into raw storage.

        Example:
            payload = broker.fetch_trade_calendar(
                calendar_year=2026,
                pm=path_manager,
            )
        """
        raw_payload = pm.raw_year_payload(
            broker=self.name,
            source_name="trade_calendar",
            calendar_year=calendar_year,
            payload_file="data.parquet",
        )
        api_name = self._TUSHARE_SOURCES["trade_calendar"]
        if not self._materialize_query(
            source_name="trade_calendar",
            api_name=api_name,
            params={
                "exchange": "SSE",
                "start_date": f"{calendar_year:04d}0101",
                "end_date": f"{calendar_year:04d}1231",
            },
            raw_payload=raw_payload,
        ):
            return None
        return raw_payload

    def _materialize_query(
        self,
        *,
        source_name: str,
        api_name: str,
        params: Mapping[str, str],
        raw_payload: Path,
    ) -> bool:
        response = self._pro.query(api_name, **dict(params))
        if response is None:
            logs.warning(
                f"⚠️ Tushare query; reason=no_data source={source_name} "
                f"api_name={api_name} params={params}"
            )
            return False
        if not isinstance(response, pandas.DataFrame):
            raise TypeError(
                f"TushareBroker response must be a DataFrame, "
                f"got={type(response).__name__}"
            )
        if source_name == "trade_calendar" and response.empty:
            logs.warning(
                f"⚠️ Tushare query; reason=no_data source={source_name} "
                f"api_name={api_name} params={params}"
            )
            return False

        payload = response.to_parquet()
        if not isinstance(payload, bytes):
            raise TypeError("TushareBroker parquet serialization returned no bytes")
        FileSystem.write_bytes_atomic(raw_payload, payload)
        return True
