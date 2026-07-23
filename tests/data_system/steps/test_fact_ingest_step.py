# filepath: tests/data_system/steps/test_fact_ingest_step.py
"""Behavior tests for offline fact ingestion."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from src.config.app_config import AppConfig
from src.data_system.brokers.base import DownloadPlan
from src.data_system.brokers.registry import BrokerRegistry
from src.data_system.context import DataContext
from src.data_system.steps.fact_ingest_step import FactIngestStep
from src.utils.path import PathManager


def test_ingest_checks_all_sources_then_rejects_partial_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def source(raw_object: str) -> SimpleNamespace:
        return SimpleNamespace(
            enabled=True,
            raw_object=raw_object,
            broker="broker",
        )

    config = SimpleNamespace(
        data=SimpleNamespace(
            sources={"missing": source("missing"), "available": source("available")}
        )
    )
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.side_effect = [object(), object()]
    path_manager.raw_payload.return_value = object()
    adapter = Mock()
    adapter.fetch.side_effect = [
        None,
        DownloadPlan(
            source_name="available",
            trade_date="2026-07-20",
            broker="broker",
            raw_object="available",
        ),
    ]
    registry = Mock(spec=BrokerRegistry)
    registry.create.return_value = adapter
    monkeypatch.setattr(
        "src.data_system.steps.fact_ingest_step.meta.load",
        Mock(return_value=None),
    )
    write = Mock()
    monkeypatch.setattr(
        "src.data_system.steps.fact_ingest_step.meta.write",
        write,
    )
    ctx = DataContext(
        trade_date="2026-07-20",
        pm=path_manager,
    )
    step = FactIngestStep(
        app_cfg=cast("AppConfig", config),
        inst=None,
        broker_registry=registry,
    )

    with pytest.raises(RuntimeError, match="only partially available"):
        step.run(ctx)
    assert adapter.fetch.call_count == 2
    write.assert_called_once()


def test_ingest_rejects_empty_source_config() -> None:
    config = SimpleNamespace(data=SimpleNamespace(sources={}))
    ctx = DataContext(
        trade_date="2026-07-20",
        pm=cast("PathManager", object()),
    )
    step = FactIngestStep(
        app_cfg=cast("AppConfig", config),
        inst=None,
        broker_registry=Mock(spec=BrokerRegistry),
    )

    with pytest.raises(ValueError, match="requires at least one source"):
        step.run(ctx)


def test_ingest_skips_when_every_source_has_no_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SimpleNamespace(enabled=True, raw_object="bars", broker="broker")
    config = SimpleNamespace(data=SimpleNamespace(sources={"bars": source}))
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.return_value = object()
    adapter = Mock()
    adapter.fetch.return_value = None
    registry = Mock(spec=BrokerRegistry)
    registry.create.return_value = adapter
    monkeypatch.setattr(
        "src.data_system.steps.fact_ingest_step.meta.load",
        Mock(return_value=None),
    )
    ctx = DataContext(trade_date="2026-07-20", pm=path_manager)
    step = FactIngestStep(
        app_cfg=cast("AppConfig", config),
        inst=None,
        broker_registry=registry,
    )

    assert step.run(ctx) is None


def test_ingest_treats_existing_raw_metadata_as_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SimpleNamespace(enabled=True, raw_object="bars", broker="broker")
    config = SimpleNamespace(data=SimpleNamespace(sources={"bars": source}))
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.return_value = object()
    registry = Mock(spec=BrokerRegistry)
    monkeypatch.setattr(
        "src.data_system.steps.fact_ingest_step.meta.load",
        Mock(return_value=object()),
    )
    ctx = DataContext(trade_date="2026-07-20", pm=path_manager)
    step = FactIngestStep(
        app_cfg=cast("AppConfig", config),
        inst=None,
        broker_registry=registry,
    )

    assert step.run(ctx) is ctx
    registry.create.assert_not_called()
