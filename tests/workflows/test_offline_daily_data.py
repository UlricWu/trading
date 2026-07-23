# filepath: tests/workflows/test_offline_daily_data.py
"""Behavior tests for offline data workflow selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from unittest.mock import Mock

import pytest

from src.config.app_config import AppConfig
from src.config.data_config import (
    BrokerConfig,
    DataConfig,
    FeatureSetConfig,
    LabelSetConfig,
    SourceConfig,
)
from src.data_system.pipeline import DataRunStatus
from src.utils.path import PathManager
from src.workflows.offline_daily_data import (
    run_offline_level2_data,
    run_offline_standard_data,
    select_offline_data_config,
)


@dataclass(frozen=True, slots=True)
class _SourceNameRegistry:
    source_names: tuple[str, ...]

    def supported_source_names(self, name: str) -> tuple[str, ...]:
        assert name == "broker"
        return self.source_names


def _app_config_with_data(data_config: DataConfig) -> AppConfig:
    """Create the smallest validated config carrier needed by selection tests."""
    return AppConfig.model_construct(data=data_config)


def _data_config(
    *,
    group: str,
    include_expansion: bool = False,
) -> DataConfig:
    sources = {
        "direct": SourceConfig(
            enabled=True,
            broker="broker",
            group=group,
            raw_object="vendor_direct",
            outputs=["direct_output"],
        ),
        "disabled": SourceConfig(
            enabled=False,
            broker="broker",
            group=group,
            raw_object="vendor_disabled",
            outputs=["disabled_output"],
        ),
    }
    if include_expansion:
        sources["expanded_sources"] = SourceConfig(
            enabled=True,
            broker="broker",
            group=group,
            use_broker_sources=True,
        )

    return DataConfig(
        brokers={"broker": BrokerConfig(normalize_profile="v1")},
        sources=sources,
        feature_sets={
            "tushare_daily_basic": FeatureSetConfig(
                enabled=True,
                version="v1",
                group=group,
            ),
            "disabled_feature": FeatureSetConfig(
                enabled=False,
                version="v1",
                group=group,
            ),
        },
        label_sets={
            "daily_t1_net_excess_rank": LabelSetConfig(
                enabled=True,
                version="v1",
                group=group,
            ),
        },
    )


def test_offline_data_selection_expands_sources_without_mutating_input() -> None:
    data_config = _data_config(
        group="offline_standard",
        include_expansion=True,
    )
    config = _app_config_with_data(data_config)

    selected = select_offline_data_config(
        app_config=config,
        group="offline_standard",
        broker_registry=_SourceNameRegistry(("expanded_a", "expanded_b")),
    )

    assert list(selected.data.sources) == [
        "direct",
        "expanded_a",
        "expanded_b",
    ]
    assert selected.data.sources["expanded_a"].raw_object == "expanded_a"
    assert selected.data.sources["expanded_a"].outputs == ["expanded_a"]
    assert list(selected.data.feature_sets) == ["tushare_daily_basic"]
    assert list(selected.data.label_sets) == ["daily_t1_net_excess_rank"]
    assert list(config.data.sources) == [
        "direct",
        "disabled",
        "expanded_sources",
    ]


def test_offline_data_selection_rejects_duplicate_effective_source_names() -> None:
    data_config = DataConfig(
        brokers={"broker": BrokerConfig(normalize_profile="v1")},
        sources={
            "expanded_a": SourceConfig(
                enabled=True,
                broker="broker",
                group="offline_standard",
                raw_object="direct_source",
                outputs=["direct_output"],
            ),
            "expanded_sources": SourceConfig(
                enabled=True,
                broker="broker",
                group="offline_standard",
                use_broker_sources=True,
            ),
        },
    )

    with pytest.raises(ValueError, match="duplicate effective source.*expanded_a"):
        select_offline_data_config(
            app_config=_app_config_with_data(data_config),
            group="offline_standard",
            broker_registry=_SourceNameRegistry(("expanded_a",)),
        )


def test_offline_data_selection_rejects_no_effective_source() -> None:
    data_config = DataConfig(
        brokers={"broker": BrokerConfig(normalize_profile="v1")},
        sources={
            "disabled": SourceConfig(
                enabled=False,
                broker="broker",
                group="offline_standard",
                raw_object="vendor_disabled",
                outputs=["disabled_output"],
            )
        },
    )

    with pytest.raises(ValueError, match="has no effective sources"):
        select_offline_data_config(
            app_config=_app_config_with_data(data_config),
            group="offline_standard",
            broker_registry=_SourceNameRegistry(()),
        )


@pytest.mark.parametrize(
    ("group", "expected_step_names"),
    [
        (
            "offline_standard",
            [
                "FactIngestStep",
                "FactNormalizeStep",
                "FeatureBuildStep",
                "LabelBuildStep",
            ],
        ),
        (
            "offline_level2",
            ["FactIngestStep", "FactNormalizeStep"],
        ),
    ],
)
def test_offline_data_entry_uses_its_owned_step_graph(
    group: str,
    expected_step_names: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _SourceNameRegistry(())
    monkeypatch.setattr(
        "src.workflows.offline_daily_data.build_broker_registry",
        lambda: registry,
    )
    path_manager = cast("PathManager", object())

    pipeline = Mock()
    pipeline.run.return_value = DataRunStatus.SUCCESS
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(
        "src.workflows.offline_daily_data.DataPipeline",
        pipeline_factory,
    )
    run_workflow = (
        run_offline_standard_data
        if group == "offline_standard"
        else run_offline_level2_data
    )

    result = run_workflow(
        app_config=_app_config_with_data(_data_config(group=group)),
        path_manager=path_manager,
        trade_date="2026-07-20",
    )

    assert result is DataRunStatus.SUCCESS
    assert [
        type(step).__name__
        for step in pipeline_factory.call_args.kwargs["steps"]
    ] == expected_step_names
    assert pipeline_factory.call_args.kwargs["pm"] is path_manager
    pipeline.run.assert_called_once_with("2026-07-20")
