# filepath: tests/data_system/brokers/test_tushare.py
"""Behavior tests for the Tushare raw-payload boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.config.app_config import AppConfig
from src.data_system.brokers import tushare as tushare_module
from src.data_system.brokers.base import DownloadPlan
from src.data_system.brokers.tushare import TushareBroker
from src.utils.path import PathManager


class _TushareClient:
    def __init__(self, response: object) -> None:
        self._DataApi__http_url = ""
        self._response = response
        self.queries: list[tuple[str, dict[str, str]]] = []

    def query(self, api_name: str, **params: str) -> object:
        self.queries.append((api_name, params))
        return self._response


def test_tushare_active_manifest_is_the_single_execution_source_list() -> None:
    assert TushareBroker.active_source_names() == (
        "trade_calendar",
        "daily_bar",
        "adj_factor",
        "daily_basic",
        "stock_basic",
        "stock_st",
        "stk_limit",
        "suspend_d",
        "cyq_perf",
        "margin",
        "margin_detail",
        "moneyflow",
        "top_list",
    )


def test_tushare_broker_returns_a_plan_for_the_materialized_raw_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _TushareClient(pd.DataFrame({"ts_code": ["000001.SZ"]}))
    monkeypatch.setattr(tushare_module.ts, "set_token", lambda token: None)
    monkeypatch.setattr(tushare_module.ts, "pro_api", lambda: client)
    app_config = SimpleNamespace(
        secret=SimpleNamespace(tushare_token="token", tushare_gateway=None)
    )
    path_manager = PathManager(tmp_path)

    fetched = TushareBroker(app_cfg=cast("AppConfig", app_config)).fetch(
        record=DownloadPlan(
            source_name="daily_bar",
            raw_object="daily_bar",
            trade_date="2026-07-20",
            broker="tushare",
        ),
        pm=path_manager,
    )

    expected_path = path_manager.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-20",
        payload_file="data.parquet",
    )
    assert fetched == DownloadPlan(
        source_name="daily_bar",
        raw_object="daily_bar",
        trade_date="2026-07-20",
        broker="tushare",
        payload_file="data.parquet",
    )
    assert client.queries == [("daily", {"trade_date": "20260720"})]
    assert pq.read_table(expected_path).to_pydict() == {"ts_code": ["000001.SZ"]}


def test_tushare_broker_queries_bak_basic_for_stock_basic_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260720"],
            "list_date": ["19910403"],
        }
    )
    client = _TushareClient(response)
    monkeypatch.setattr(tushare_module.ts, "set_token", lambda token: None)
    monkeypatch.setattr(tushare_module.ts, "pro_api", lambda: client)
    app_config = SimpleNamespace(
        secret=SimpleNamespace(tushare_token="token", tushare_gateway=None)
    )
    path_manager = PathManager(tmp_path)

    fetched = TushareBroker(app_cfg=cast("AppConfig", app_config)).fetch(
        record=DownloadPlan(
            source_name="stock_basic",
            raw_object="stock_basic",
            trade_date="2026-07-20",
            broker="tushare",
        ),
        pm=path_manager,
    )

    expected_path = path_manager.raw_payload(
        broker="tushare",
        source_name="stock_basic",
        trade_date="2026-07-20",
        payload_file="data.parquet",
    )
    assert fetched == DownloadPlan(
        source_name="stock_basic",
        raw_object="stock_basic",
        trade_date="2026-07-20",
        broker="tushare",
        payload_file="data.parquet",
    )
    assert client.queries == [("bak_basic", {"trade_date": "20260720"})]
    assert pq.ParquetFile(expected_path).read().to_pydict() == response.to_dict(
        orient="list"
    )


def test_tushare_broker_queries_one_calendar_year(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = pd.DataFrame(
        {
            "cal_date": ["20260101", "20260102"],
            "is_open": [0, 1],
        }
    )
    client = _TushareClient(response)
    monkeypatch.setattr(tushare_module.ts, "set_token", lambda token: None)
    monkeypatch.setattr(tushare_module.ts, "pro_api", lambda: client)
    app_config = SimpleNamespace(
        secret=SimpleNamespace(tushare_token="token", tushare_gateway=None)
    )
    path_manager = PathManager(tmp_path)
    broker = TushareBroker(app_cfg=cast("AppConfig", app_config))

    payload = broker.fetch_trade_calendar(
        calendar_year=2026,
        pm=path_manager,
    )

    expected_path = path_manager.raw_year_payload(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
        payload_file="data.parquet",
    )
    assert payload == expected_path
    assert client.queries == [
        (
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": "20260101",
                "end_date": "20261231",
            },
        )
    ]
    assert pq.read_table(expected_path).to_pydict() == response.to_dict(
        orient="list"
    )


def test_tushare_broker_translates_an_empty_response_to_no_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _TushareClient(pd.DataFrame())
    monkeypatch.setattr(tushare_module.ts, "set_token", lambda token: None)
    monkeypatch.setattr(tushare_module.ts, "pro_api", lambda: client)
    app_config = SimpleNamespace(
        secret=SimpleNamespace(tushare_token="token", tushare_gateway=None)
    )

    fetched = TushareBroker(app_cfg=cast("AppConfig", app_config)).fetch(
        record=DownloadPlan(
            source_name="daily_bar",
            raw_object="daily_bar",
            trade_date="2026-07-20",
            broker="tushare",
        ),
        pm=PathManager(tmp_path),
    )

    assert fetched is None
