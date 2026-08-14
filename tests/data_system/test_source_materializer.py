# filepath: tests/data_system/test_source_materializer.py
"""Behavior tests for raw-to-processed source materialization."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pyarrow as pa
import pytest

from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system import source_materializer as materializer_module
from src.data_system.brokers.base import BrokerAdapter, DownloadPlan
from src.data_system.normalize.profiles import NormalizeOutput
from src.data_system.source_materializer import SourceMaterializer
from src.utils.path import PathManager


def _source(raw_object: str, *, outputs: list[str] | None = None) -> SourceConfig:
    return SourceConfig(
        enabled=True,
        broker="broker",
        group="offline_standard",
        raw_object=raw_object,
        outputs=outputs if outputs is not None else [raw_object],
    )


def test_materializer_attempts_every_source_then_rejects_partial_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    path_manager.raw_meta.side_effect = [object(), object()]
    path_manager.raw_payload.return_value = object()
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.side_effect = [
        None,
        DownloadPlan(
            source_name="available",
            trade_date="2026-07-20",
            broker="broker",
            raw_object="available",
        ),
    ]
    adapter_class = Mock(return_value=adapter)
    monkeypatch.setattr(materializer_module.meta, "find", Mock(return_value=None))
    commit = Mock()
    monkeypatch.setattr(materializer_module.meta, "commit", commit)
    materializer = SourceMaterializer(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={
            "missing": _source("missing"),
            "available": _source("available"),
        },
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_profiles={"broker": Mock()},
        processed_version="v1",
        adapter_cache={},
    )

    with pytest.raises(RuntimeError, match="only partially available"):
        materializer.materialize("2026-07-20")

    assert adapter.fetch.call_count == 2
    adapter_class.assert_called_once()
    commit.assert_called_once()


def test_materializer_returns_false_without_normalizing_when_all_sources_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.return_value = None
    normalize = Mock()
    monkeypatch.setattr(materializer_module.meta, "find", Mock(return_value=None))
    materializer = SourceMaterializer(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"bars": _source("bars")},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock(return_value=adapter)},
        ),
        normalize_profiles={"broker": normalize},
        processed_version="v1",
        adapter_cache={},
    )

    assert materializer.materialize("2026-07-20") is False
    normalize.assert_not_called()


def test_materializer_meta_hit_does_not_construct_a_broker_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    adapter_class = Mock()
    monkeypatch.setattr(materializer_module.meta, "find", Mock(return_value=object()))
    materializer = SourceMaterializer(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"bars": _source("bars", outputs=[])},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_profiles={},
        processed_version="v1",
        adapter_cache={},
    )

    assert materializer.materialize("2026-07-20") is True
    adapter_class.assert_not_called()


def test_materializer_reuses_one_lazy_adapter_across_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_manager = Mock(spec=PathManager)
    adapter = Mock(spec=BrokerAdapter)
    adapter.fetch.return_value = None
    adapter_class = Mock(return_value=adapter)
    monkeypatch.setattr(materializer_module.meta, "find", Mock(return_value=None))
    materializer = SourceMaterializer(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"bars": _source("bars")},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": adapter_class},
        ),
        normalize_profiles={"broker": Mock()},
        processed_version="v1",
        adapter_cache={},
    )

    assert materializer.materialize("2026-07-20") is False
    assert materializer.materialize("2026-07-21") is False
    adapter_class.assert_called_once()


def test_materializer_uses_staging_only_when_size_matches_raw(
    tmp_path: Path,
) -> None:
    path_manager = PathManager(tmp_path)
    trade_date = "2026-05-01"
    raw_path = path_manager.raw_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file="source.csv.7z",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"same-size")
    meta.commit(pm=path_manager, payload_path=raw_path)
    staging_path = path_manager.staging_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file=raw_path.name,
    )
    staging_path.parent.mkdir(parents=True)
    staging_path.write_bytes(b"same-size")
    selected_inputs: list[Path] = []

    def normalize_profile(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        selected_inputs.append(input_file)
        return NormalizeOutput(table=pa.table({"value": [1]}))

    materializer = SourceMaterializer(
        app_config=cast("AppConfig", object()),
        path_manager=path_manager,
        sources={"source": _source("raw_object", outputs=["output"])},
        broker_classes=cast(
            "dict[str, type[BrokerAdapter]]",
            {"broker": Mock()},
        ),
        normalize_profiles={"broker": normalize_profile},
        processed_version="v1",
        adapter_cache={},
    )

    assert materializer.materialize(trade_date) is True
    assert selected_inputs == [staging_path]


def test_materializer_rejects_an_unknown_profile_before_date_io(
    tmp_path: Path,
) -> None:
    with pytest.raises(KeyError, match="not registered"):
        SourceMaterializer(
            app_config=cast("AppConfig", object()),
            path_manager=PathManager(tmp_path),
            sources={"source": _source("source")},
            broker_classes=cast(
                "dict[str, type[BrokerAdapter]]",
                {"broker": Mock()},
            ),
            normalize_profiles={},
            processed_version="v1",
            adapter_cache={},
        )
