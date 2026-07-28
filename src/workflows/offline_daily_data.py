# filepath: src/workflows/offline_daily_data.py
"""Assemble and execute the two fixed offline data workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Protocol

from src import logs
from src.config.app_config import AppConfig
from src.config.data_config import DataConfig, SourceConfig
from src.data_system.brokers.bootstrap import build_broker_registry
from src.data_system.context import DataContext
from src.data_system.steps.fact_ingest_step import FactIngestStep
from src.data_system.steps.fact_normalize_step import FactNormalizeStep
from src.data_system.steps.feature_build_step import FeatureBuildStep
from src.data_system.steps.label_build_step import LabelBuildStep
from src.observability.instrumentation import Instrumentation
from src.pipeline import run_steps
from src.utils.path import PathManager


OFFLINE_STANDARD = "offline_standard"
OFFLINE_LEVEL2 = "offline_level2"
OFFLINE_STANDARD_FEATURE_SETS = frozenset({"tushare_daily_basic"})
OFFLINE_STANDARD_LABEL_SETS = frozenset(
    {
        "daily_t1_net_excess_rank",
        "daily_forward_excess_rank",
    }
)


class DataRunStatus(StrEnum):
    """Describe the public outcome of one offline data workflow.

    Example:
        status = DataRunStatus.SKIPPED
    """

    SUCCESS = "success"
    SKIPPED = "skipped"


class SourceNameRegistry(Protocol):
    """Expose broker-native source names used during config expansion.

    Example:
        names = broker_registry.supported_source_names("level2")
    """

    def supported_source_names(self, name: str) -> tuple[str, ...]:
        """Return the source names supported by one broker.

        Example:
            names = broker_registry.supported_source_names("level2")
        """
        ...


def select_offline_data_config(
    *,
    app_config: AppConfig,
    group: str,
    broker_registry: SourceNameRegistry,
) -> AppConfig:
    """Return the selected config for one fixed offline data workflow.

    Example:
        selected = select_offline_data_config(
            app_config=app_config,
            group=OFFLINE_STANDARD,
            broker_registry=broker_registry,
        )
    """
    if group not in {OFFLINE_STANDARD, OFFLINE_LEVEL2}:
        raise ValueError(f"unsupported offline data group: {group}")

    selected_sources: dict[str, SourceConfig] = {}
    for source_name, source_config in app_config.data.sources.items():
        if source_config.group != group:
            continue
        if not source_config.enabled:
            logs.warning(
                f"[OfflineData] skip source={source_name} group={group} "
                f"broker={source_config.broker} reason=source disabled"
            )
            continue

        effective_sources: Sequence[tuple[str, SourceConfig]]
        if source_config.use_broker_sources:
            effective_sources = tuple(
                (
                    dataset_name,
                    SourceConfig(
                        enabled=True,
                        broker=source_config.broker,
                        group=group,
                        raw_object=dataset_name,
                        outputs=[dataset_name],
                    ),
                )
                for dataset_name in broker_registry.supported_source_names(
                    source_config.broker
                )
            )
        else:
            effective_sources = ((source_name, source_config),)

        for effective_name, effective_config in effective_sources:
            if effective_name in selected_sources:
                raise ValueError(
                    "offline data group produced duplicate effective source "
                    f"name: {effective_name!r}"
                )
            selected_sources[effective_name] = effective_config

    if not selected_sources:
        raise ValueError(f"offline data group '{group}' has no effective sources")

    selected_data = DataConfig(
        brokers=app_config.data.brokers,
        sources=selected_sources,
        feature_sets={
            name: feature_config
            for name, feature_config in app_config.data.feature_sets.items()
            if feature_config.enabled and feature_config.group == group
        },
        label_sets={
            name: label_config
            for name, label_config in app_config.data.label_sets.items()
            if label_config.enabled and label_config.group == group
        },
    )
    return app_config.model_copy(update={"data": selected_data}, deep=True)


def run_offline_standard_data(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    trade_date: str,
) -> DataRunStatus:
    """Run the fixed standard ingest, normalize, feature, and label flow.

    Example:
        status = run_offline_standard_data(
            app_config=app_config,
            path_manager=path_manager,
            trade_date="2026-07-20",
        )
    """
    broker_registry = build_broker_registry()
    selected_config = select_offline_data_config(
        app_config=app_config,
        group=OFFLINE_STANDARD,
        broker_registry=broker_registry,
    )
    context = DataContext(
        trade_date=trade_date,
        pm=path_manager,
    )
    ingest_step = FactIngestStep(
        app_cfg=selected_config,
        broker_registry=broker_registry,
    )
    remaining_steps: tuple[Callable[[DataContext], None], ...] = (
        FactNormalizeStep(app_cfg=selected_config),
        FeatureBuildStep(
            app_cfg=selected_config,
            allowed_sets=OFFLINE_STANDARD_FEATURE_SETS,
        ),
        LabelBuildStep(
            app_cfg=selected_config,
            allowed_sets=OFFLINE_STANDARD_LABEL_SETS,
        ),
    )

    logs.info(f"[OfflineData] started trade_date={trade_date}")
    with Instrumentation(trade_date) as instrumentation:
        if not instrumentation.call(ingest_step, context):
            logs.warning(
                f"[OfflineData] skipped trade_date={trade_date} "
                f"reason=no_source_payload"
            )
            return DataRunStatus.SKIPPED
        run_steps(context, remaining_steps, instrumentation)

    logs.info(f"[OfflineData] finished trade_date={trade_date}")
    return DataRunStatus.SUCCESS


def run_offline_level2_data(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    trade_date: str,
) -> DataRunStatus:
    """Run the fixed Level-2 ingest and normalize flow.

    Example:
        status = run_offline_level2_data(
            app_config=app_config,
            path_manager=path_manager,
            trade_date="2026-07-20",
        )
    """
    broker_registry = build_broker_registry()
    selected_config = select_offline_data_config(
        app_config=app_config,
        group=OFFLINE_LEVEL2,
        broker_registry=broker_registry,
    )
    context = DataContext(
        trade_date=trade_date,
        pm=path_manager,
    )
    ingest_step = FactIngestStep(
        app_cfg=selected_config,
        broker_registry=broker_registry,
    )
    remaining_steps: tuple[Callable[[DataContext], None], ...] = (
        FactNormalizeStep(app_cfg=selected_config),
    )

    logs.info(f"[OfflineData] started trade_date={trade_date}")
    with Instrumentation(trade_date) as instrumentation:
        if not instrumentation.call(ingest_step, context):
            logs.warning(
                f"[OfflineData] skipped trade_date={trade_date} "
                f"reason=no_source_payload"
            )
            return DataRunStatus.SKIPPED
        run_steps(context, remaining_steps, instrumentation)

    logs.info(f"[OfflineData] finished trade_date={trade_date}")
    return DataRunStatus.SUCCESS
