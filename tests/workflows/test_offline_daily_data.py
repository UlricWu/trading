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
from src.jobs.requests import (
    DataJobKind,
    DataSubmission,
    FeatureBackfillSubmission,
    Level2MinuteBackfillSubmission,
    StandardFactBootstrapSubmission,
)
from src.utils.path import PathManager
from src.workflows import offline_daily_data as workflow_module
from src.workflows.offline_daily_data import (
    run_feature_backfill,
    run_level2_minute_backfill,
    run_offline_data,
    run_standard_fact_bootstrap,
)


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


def test_standard_workflow_uses_only_enabled_feature_and_label_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    enabled_feature = FeatureSetConfig(
        enabled=True,
        version="v1",
    )
    disabled_feature = FeatureSetConfig(
        enabled=False,
        version="v1",
    )
    enabled_label = LabelSetConfig(
        enabled=True,
        version="v1",
    )
    disabled_label = LabelSetConfig(
        enabled=False,
        version="v1",
    )
    config.data.feature_sets = {
        "enabled-feature": enabled_feature,
        "disabled-feature": disabled_feature,
    }
    config.data.label_sets = {
        "enabled-label": enabled_label,
        "disabled-label": disabled_label,
    }
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
            kind="data-standard",
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    assert feature_step_factory.call_args.kwargs["feature_versions"] == {
        "enabled-feature": enabled_feature.version,
    }
    assert label_step_factory.call_args.kwargs["label_versions"] == {
        "enabled-label": enabled_label.version,
    }
    pipeline.run.assert_called_once()


def test_standard_workflow_accepts_all_derived_operations_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    config.data.feature_sets = {
        "disabled-feature": FeatureSetConfig(enabled=False, version="v1"),
    }
    config.data.label_sets = {
        "disabled-label": LabelSetConfig(enabled=False, version="v1"),
    }
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    feature_step_factory = Mock()
    label_step_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))
    monkeypatch.setattr(workflow_module, "FeatureBuildStep", feature_step_factory)
    monkeypatch.setattr(workflow_module, "LabelBuildStep", label_step_factory)

    run_offline_data(
        app_config=config,
        path_manager=cast("PathManager", object()),
        submission=DataSubmission(
            kind="data-standard",
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    assert feature_step_factory.call_args.kwargs["feature_versions"] == {}
    assert label_step_factory.call_args.kwargs["label_versions"] == {}
    assert [call.args[0] for call in logger.warning.call_args_list] == [
        "⚠️ workflow selection; kind=data-standard "
        "operation=feature reason=no_enabled_config",
        "⚠️ workflow selection; kind=data-standard "
        "operation=label reason=no_enabled_config",
    ]
    pipeline.run.assert_called_once()


def test_standard_workflow_rejects_enabled_unknown_derived_identity_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    config.data.feature_sets = {
        "unknown": FeatureSetConfig(enabled=True, version="v1"),
    }
    pipeline_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)

    with pytest.raises(ValueError, match="unknown feature builder"):
        run_offline_data(
            app_config=config,
            path_manager=PathManager(tmp_path),
            submission=DataSubmission(
                kind="data-standard",
                start="2026-07-20",
                end="2026-07-20",
            ),
        )

    pipeline_factory.assert_not_called()


def test_level2_workflow_keeps_empty_feature_and_label_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    config.data.feature_sets = {
        "enabled-feature": FeatureSetConfig(enabled=True, version="v1"),
    }
    config.data.label_sets = {
        "enabled-label": LabelSetConfig(enabled=True, version="v1"),
    }
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    feature_step_factory = Mock()
    label_step_factory = Mock()
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))
    monkeypatch.setattr(workflow_module, "FeatureBuildStep", feature_step_factory)
    monkeypatch.setattr(workflow_module, "LabelBuildStep", label_step_factory)

    run_offline_data(
        app_config=config,
        path_manager=cast("PathManager", object()),
        submission=DataSubmission(
            kind="data-level2",
            start="2026-07-20",
            end="2026-07-20",
        ),
    )

    assert feature_step_factory.call_args.kwargs["feature_versions"] == {}
    assert label_step_factory.call_args.kwargs["label_versions"] == {}
    pipeline.run.assert_called_once()


def test_standard_fact_bootstrap_runs_only_calendar_and_standard_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)
    calendar_step = object()
    fact_step = object()
    monkeypatch.setattr(
        workflow_module,
        "CalendarMaterializeStep",
        Mock(return_value=calendar_step),
    )
    fact_step_factory = Mock(return_value=fact_step)
    monkeypatch.setattr(workflow_module, "FactMaterializeStep", fact_step_factory)
    monkeypatch.setattr(
        workflow_module.TushareBroker,
        "active_source_names",
        Mock(return_value=("trade_calendar", "daily_bar", "daily_basic")),
    )

    run_standard_fact_bootstrap(
        app_config=_app_config(),
        path_manager=cast("PathManager", object()),
        submission=StandardFactBootstrapSubmission(
            start="2019-01-01",
            end="2019-04-03",
        ),
    )

    assert pipeline_factory.call_args.kwargs["steps"] == (calendar_step, fact_step)
    assert list(fact_step_factory.call_args.kwargs["sources"]) == [
        "daily_bar",
        "daily_basic",
    ]
    pipeline.run.assert_called_once_with(
        DataContext(start="2019-01-01", end="2019-04-03")
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "▶️ workflow; kind=data-standard-bootstrap start=2019-01-01 "
        "end=2019-04-03",
        "✅ workflow; kind=data-standard-bootstrap start=2019-01-01 "
        "end=2019-04-03",
    ]


def test_feature_backfill_resolves_targets_and_runs_only_one_feature_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    access = Mock()
    access.trade_dates.return_value = ["2019-04-04", "2019-04-08"]
    monkeypatch.setattr(workflow_module, "Access", Mock(return_value=access))
    feature_step = object()
    feature_step_factory = Mock(return_value=feature_step)
    monkeypatch.setattr(workflow_module, "FeatureBuildStep", feature_step_factory)
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)
    path_manager = cast("PathManager", object())

    run_feature_backfill(
        path_manager=path_manager,
        submission=FeatureBackfillSubmission(
            feature_set="tushare_daily_basic",
            version="v1",
            start="2019-04-04",
            end="2019-04-08",
        ),
    )

    access.trade_dates.assert_called_once_with(
        start_date="2019-04-04",
        end_date="2019-04-08",
    )
    assert feature_step_factory.call_args.kwargs["pm"] is path_manager
    assert feature_step_factory.call_args.kwargs["access"] is access
    assert feature_step_factory.call_args.kwargs["feature_versions"] == {
        "tushare_daily_basic": "v1",
    }
    assert pipeline_factory.call_args.kwargs["steps"] == (feature_step,)
    pipeline.run.assert_called_once_with(
        DataContext(
            start="2019-04-04",
            end="2019-04-08",
            trade_dates=("2019-04-04", "2019-04-08"),
        )
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "▶️ workflow; kind=data-feature-backfill "
        "feature_set=tushare_daily_basic version=v1 start=2019-04-04 "
        "end=2019-04-08 targets=2",
        "✅ workflow; kind=data-feature-backfill "
        "feature_set=tushare_daily_basic version=v1 start=2019-04-04 "
        "end=2019-04-08 targets=2",
    ]


def test_feature_backfill_rejects_unknown_identity_before_calendar_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = Mock()
    monkeypatch.setattr(workflow_module, "Access", Mock(return_value=access))

    with pytest.raises(ValueError, match="unknown feature builder"):
        run_feature_backfill(
            path_manager=PathManager(tmp_path),
            submission=FeatureBackfillSubmission(
                feature_set="unknown",
                version="v1",
                start="2019-04-03",
                end="2019-04-04",
            ),
        )

    access.trade_dates.assert_not_called()


def test_feature_backfill_accepts_an_empty_formal_target_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = Mock()
    access.trade_dates.return_value = []
    monkeypatch.setattr(workflow_module, "Access", Mock(return_value=access))
    feature_step = object()
    monkeypatch.setattr(
        workflow_module,
        "FeatureBuildStep",
        Mock(return_value=feature_step),
    )
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    monkeypatch.setattr(workflow_module, "DataPipeline", Mock(return_value=pipeline))

    run_feature_backfill(
        path_manager=cast("PathManager", object()),
        submission=FeatureBackfillSubmission(
            feature_set="tushare_daily_basic",
            version="v1",
            start="2019-01-01",
            end="2019-01-01",
        ),
    )

    pipeline.run.assert_called_once_with(
        DataContext(
            start="2019-01-01",
            end="2019-01-01",
            trade_dates=(),
        )
    )


@pytest.mark.parametrize(
    "resolved_dates",
    [("2025-11-18", "2025-11-19"), ()],
)
def test_level2_minute_backfill_runs_only_one_minute_step(
    resolved_dates: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr(workflow_module, "logs", logger)
    access = Mock()
    access.trade_dates.return_value = list(resolved_dates)
    access_factory = Mock(return_value=access)
    monkeypatch.setattr(workflow_module, "Access", access_factory)
    minute_step = object()
    minute_step_factory = Mock(return_value=minute_step)
    monkeypatch.setattr(
        workflow_module,
        "Level2MinuteBuildStep",
        minute_step_factory,
    )
    pipeline = Mock(spec=DataPipeline)
    pipeline.run.side_effect = lambda context: context
    pipeline_factory = Mock(return_value=pipeline)
    monkeypatch.setattr(workflow_module, "DataPipeline", pipeline_factory)
    path_manager = cast("PathManager", object())

    run_level2_minute_backfill(
        path_manager=path_manager,
        submission=Level2MinuteBackfillSubmission(
            start="2025-11-18",
            end="2025-11-19",
        ),
    )

    access_factory.assert_called_once_with(
        pm=path_manager,
        processed_version="v1",
    )
    access.trade_dates.assert_called_once_with(
        start_date="2025-11-18",
        end_date="2025-11-19",
    )
    assert minute_step_factory.call_args.kwargs == {
        "pm": path_manager,
        "access": access,
        "processed_version": "v1",
        "symbol_batch_size": 16,
    }
    assert pipeline_factory.call_args.kwargs["steps"] == (minute_step,)
    pipeline.run.assert_called_once_with(
        DataContext(
            start="2025-11-18",
            end="2025-11-19",
            trade_dates=resolved_dates,
        )
    )
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "▶️ workflow; kind=data-level2-minute-backfill start=2025-11-18 "
        f"end=2025-11-19 targets={len(resolved_dates)}",
        "✅ workflow; kind=data-level2-minute-backfill start=2025-11-18 "
        f"end=2025-11-19 targets={len(resolved_dates)}",
    ]


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
