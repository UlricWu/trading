# filepath: src/data_system/brokers/tushare.py
"""Tushare broker implementation for configured raw-object ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar, Protocol

import pandas
import tushare as ts

from src.config.app_config import AppConfig
from src.data_system.brokers.base import DownloadPlan
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src import logs
from src.utils.path import PathManager


class _TushareClient(Protocol):
    _DataApi__http_url: str

    def query(self, api_name: str, **params: str) -> object: ...


class TushareBroker:
    """
    Materialize one Tushare raw object response directly into raw.

    The broker owns the supported Tushare source registry and source-side no-data
    translation. It does not normalize, commit metadata, or choose formal raw
    object identity; those responsibilities remain outside this adapter.
    """

    name = "tushare"

    _TUSHARE_SOURCES: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "trade_calendar": "trade_cal",
            "daily_bar": "daily",
            "adj_factor": "adj_factor",
            "daily_basic": "daily_basic",
            "stock_basic": "stock_basic",
            "stock_st": "stock_st",
            "stk_limit": "stk_limit",
            "suspend_d": "suspend_d",
            "cyq_perf": "cyq_perf",
            "margin": "margin",
            "margin_detail": "margin_detail",
            "moneyflow": "moneyflow",
            # "moneyflow_hsgt": "moneyflow_hsgt",
            # todo 2026-07-01 是香港特别行政区成立纪念日，港股和南北向互联互通均休市，因此该空结果符合当日市场安排
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
    def supported_source_names(cls) -> tuple[str, ...]:
        """Return Tushare source names supported by the broker registry."""
        return tuple(cls._TUSHARE_SOURCES)

    def fetch(
        self,
        *,
        record: DownloadPlan,
        pm: PathManager,
    ) -> DownloadPlan | None:
        """Fetch one Tushare raw object as raw `data.parquet`."""

        if record.raw_object not in self._TUSHARE_SOURCES:
            raise ValueError(
                f"TushareBroker does not support raw_object={record.raw_object!r} "
                f"for source_name={record.source_name!r}"
            )

        api_name = self._TUSHARE_SOURCES[record.raw_object]

        params = {"trade_date": DateTimeUtils.to_compact_date(record.trade_date)}
        response = self._pro.query(api_name, **params)

        if response is None:
            logs.info(
                f"[TushareBroker] no data source={record.source_name} "
                f"api name={api_name} "
                f"params={params}"
            )
            return None
        if not isinstance(response, pandas.DataFrame):
            raise TypeError(
                f"TushareBroker response must be a DataFrame, "
                f"got={type(response).__name__}"
            )
        if response.empty:
            logs.info(
                f"[TushareBroker] no data source={record.source_name} "
                f"api_name={api_name} params={params}"
            )
            return None

        payload_file = record.payload_file
        raw_payload = pm.raw_payload(
            broker=record.broker,
            trade_date=record.trade_date,
            source_name=record.source_name,
            payload_file=payload_file,
        )
        logs.info(f"[TushareBroker] fetched raw_payload={raw_payload}")
        payload = response.to_parquet()
        if not isinstance(payload, bytes):
            raise RuntimeError("TushareBroker parquet serialization returned no bytes")
        FileSystem.write_bytes_atomic(raw_payload, payload)

        return DownloadPlan(
            trade_date=record.trade_date,
            broker=record.broker,
            source_name=record.source_name,
            raw_object=record.raw_object,
            payload_file=raw_payload.name,
        )
