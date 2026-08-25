# filepath: tests/workflows/test_offline_daily_data.py
"""Composition tests for the unified offline data workflow graph."""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock

import pytest

from src.config.app_config import AppConfig
from src.config.data_config import (
    BrokerConfig,
    DataConfig,
    DownloadBackend,
    FeatureSetConfig,
    LabelSetConfig,
    SourceConfig,
)
from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.jobs.requests import DataJobKind, DataSubmission
from src.utils.path import PathManager
from src.workflows import offline_daily_data as workflow_module
from src.workflows.offline_daily_data import run_offline_data


def _app_config() -> AppConfig:
    return AppConfig.model_construct(
        data=DataConfig(
            brokers={
                "tushare": BrokerConfig(),
                "level2_ftp": BrokerConfig(
                    remote_root="level2",
                    ftp_backend=DownloadBackend.FTPLIB,
                ),
            },
            sources={
                "sh_trade": SourceConfig(
                    enabled=True,
                    broker="level2_ftp",
                    group="offline_level2",
                    raw_object="SH_Stock_OrderTrade",
                    outputs=["sh_trade"],
                ),
            },
        )
    )


@pytest.mark.parametrize("kind", ["data-standard", "data-level2"])
def test_data_workflow_supplies_one_linear_domain_step_sequence(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)
    path_manager = cast("PathManager", object())
    submission = DataSubmission(
        kind=cast("DataJobKind", kind),
        start="2026-07-20",
        end="2026-07-20",
    )

    result = run_offline_data(
        app_config=_app_config(),
        path_manager=path_manager,
        submission=submission,
    )

    assert result is None
    assert [
        type(step).__name__ for step in pipeline_factory.call_args.kwargs["steps"]
    ] == [
        "CalendarMaterializeStep",
        "FactMaterializeStep",
        "FeatureBuildStep",
        "LabelBuildStep",
    ]
    pipeline.run.assert_called_once()
    context = pipeline.run.call_args.args[0]
    assert context == DataContext(start="2026-07-20", end="2026-07-20")
    assert [call.args[0] for call in logger.info.call_args_list] == [
        f"▶️ workflow; kind={kind} start=2026-07-20 end=2026-07-20",
        f"✅ workflow; kind={kind} start=2026-07-20 end=2026-07-20",
    ]


def test_standard_sources_come_only_from_the_tushare_active_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    fact_step_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))
    monkeypatch.setattr(
        workflow_module,
        "FactMaterializeStep",
        fact_step_factory,
    )
    monkeypatch.setattr(
        workflow_module.TushareBroker,
        "active_source_names",
        Mock(return_value=("trade_calendar", "daily_bar", "daily_basic")),
    )

    run_offline_data(
        app_config=_app_config(),
        path_manager=cast("PathManager", object()),
        submission=DataSubmission(
            kind="data-standard",
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    fact_sources = fact_step_factory.call_args.kwargs["sources"]
    assert list(fact_sources) == ["daily_bar", "daily_basic"]
    assert all(source.broker == "tushare" for source in fact_sources.values())
    assert all(
        source.raw_object == name and source.outputs == [name]
        for name, source in fact_sources.items()
    )


def test_level2_sources_come_only_from_enabled_file_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    config.data.sources["disabled"] = SourceConfig(
        enabled=False,
        broker="level2_ftp",
        group="offline_level2",
        raw_object="SZ_Order",
        outputs=[],
    )
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    fact_step_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))
    monkeypatch.setattr(
        workflow_module,
        "FactMaterializeStep",
        fact_step_factory,
    )

    run_offline_data(
        app_config=config,
        path_manager=cast("PathManager", object()),
        submission=DataSubmission(
            kind="data-level2",
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    fact_sources = fact_step_factory.call_args.kwargs["sources"]
    assert list(fact_sources) == ["sh_trade"]


@pytest.mark.parametrize("kind", ["data-standard", "data-level2"])
def test_data_workflow_uses_empty_feature_and_label_operations(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    config.data.feature_sets["not-designed"] = FeatureSetConfig(
        enabled=True,
        version="v1",
    )
    config.data.label_sets["not-designed"] = LabelSetConfig(
        enabled=True,
        version="v1",
    )
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    feature_step_factory = Mock()
    label_step_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))
    monkeypatch.setattr(
        workflow_module,
        "FeatureBuildStep",
        feature_step_factory,
    )
    monkeypatch.setattr(
        workflow_module,
        "LabelBuildStep",
        label_step_factory,
    )

    run_offline_data(
        app_config=config,
        path_manager=cast("PathManager", object()),
        submission=DataSubmission(
            kind=cast("DataJobKind", kind),
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    assert feature_step_factory.call_args.kwargs["feature_sets"] == {}
    assert label_step_factory.call_args.kwargs["label_sets"] == {}
    pipeline.run.assert_called_once()


def test_data_workflow_rejects_an_invalid_kind_before_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)

    with pytest.raises(ValueError, match="data-standard.*data-level2"):
        run_offline_data(
            app_config=_app_config(),
            path_manager=cast("PathManager", object()),
            submission=DataSubmission(
                kind=cast("DataJobKind", "unknown"),
                start="2026-07-20",
                end="2026-07-20",
            ),
        )

    pipeline_factory.assert_not_called()
