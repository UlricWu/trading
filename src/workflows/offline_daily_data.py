# filepath: src/workflows/offline_daily_data.py
"""Execute fixed range-based Standard, Level-2, and derived-data workflows."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src import logs
from src.access import Access
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.brokers.catalog import BROKER_ADAPTER_CLASSES
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.brokers.tushare import TushareBroker
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOperation
from src.data_system.normalize.level2 import normalize_level2
from src.data_system.normalize.tushare import normalize_tushare
from src.data_system.pipeline import DataPipeline
from src.data_system.steps.calendar_materialize import CalendarMaterializeStep
from src.data_system.steps.fact_materialize import FactMaterializeStep
from src.data_system.steps.feature_build import FeatureBuildStep
from src.data_system.steps.label_build import LabelBuildStep
from src.data_system.steps.level2_minute_build import Level2MinuteBuildStep
from src.jobs.requests import (
    DataSubmission,
    FeatureBackfillSubmission,
    Level2MinuteBackfillSubmission,
    StandardFactBootstrapSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION

OFFLINE_STANDARD = "offline_standard"
OFFLINE_LEVEL2 = "offline_level2"
_LEVEL2_MINUTE_SYMBOL_BATCH_SIZE = 16
_NORMALIZE_OPERATIONS: Mapping[str, NormalizeOperation] = MappingProxyType(
    {
        TushareBroker.name: normalize_tushare,
        Level2Broker.name: normalize_level2,
    }
)


def _require_tushare_source_names() -> tuple[str, ...]:
    source_names = TushareBroker.active_source_names()
    if "trade_calendar" not in source_names:
        raise ValueError("Tushare active manifest requires trade_calendar")
    return source_names


def _standard_fact_sources() -> dict[str, SourceConfig]:
    fact_sources = {
        source_name: SourceConfig(
            enabled=True,
            broker=TushareBroker.name,
            group=OFFLINE_STANDARD,
            raw_object=source_name,
            outputs=[source_name],
        )
        for source_name in _require_tushare_source_names()
        if source_name != "trade_calendar"
    }
    if not fact_sources:
        raise ValueError("offline data kind 'data-standard' has no fact sources")
    return fact_sources


def run_offline_data(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    submission: DataSubmission,
) -> None:
    """Materialize one complete Standard or Level-2 data range.

    Example:
        run_offline_data(
            app_config=app_config,
            path_manager=path_manager,
            submission=DataSubmission(
                kind="data-standard",
                start="2026-07-01",
                end="2026-07-20",
            ),
        )
    """
    if submission.kind not in ("data-standard", "data-level2"):
        raise ValueError(
            "run_offline_data requires kind='data-standard' or 'data-level2'"
        )
    if submission.kind == "data-standard":
        fact_sources = _standard_fact_sources()
        feature_versions = {
            feature_set: config.version
            for feature_set, config in app_config.data.feature_sets.items()
            if config.enabled
        }
        label_versions = {
            label_set: config.version
            for label_set, config in app_config.data.label_sets.items()
            if config.enabled
        }
        if not feature_versions:
            logs.warning(
                "⚠️ workflow selection; kind=data-standard "
                "operation=feature reason=no_enabled_config"
            )
        if not label_versions:
            logs.warning(
                "⚠️ workflow selection; kind=data-standard "
                "operation=label reason=no_enabled_config"
            )
    else:
        _require_tushare_source_names()
        fact_sources = {}
        for source_name, source_config in app_config.data.sources.items():
            if not source_config.enabled:
                logs.warning(
                    f"⚠️ source; source={source_name} group={OFFLINE_LEVEL2} "
                    f"broker={source_config.broker} reason=disabled"
                )
                continue
            fact_sources[source_name] = source_config
        feature_versions = {}
        label_versions = {}

    if not fact_sources:
        raise ValueError(f"offline data kind '{submission.kind}' has no fact sources")

    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    adapter_cache: dict[str, BrokerAdapter] = {}
    steps: tuple[PipelineStep[DataContext], ...] = (
        CalendarMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            access=access,
            processed_version=PROCESSED_VERSION,
            adapter_cache=adapter_cache,
        ),
        FactMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            sources=fact_sources,
            broker_classes=BROKER_ADAPTER_CLASSES,
            normalize_operations=_NORMALIZE_OPERATIONS,
            processed_version=PROCESSED_VERSION,
            adapter_cache=adapter_cache,
        ),
        FeatureBuildStep(
            pm=path_manager,
            access=access,
            feature_versions=feature_versions,
        ),
        LabelBuildStep(
            pm=path_manager,
            access=access,
            label_versions=label_versions,
        ),
    )
    instrumentation = Instrumentation(
        f"{submission.kind}_{submission.start}_{submission.end}"
    )
    pipeline = DataPipeline(
        steps=steps,
        instrumentation=instrumentation,
    )
    logs.info(
        f"▶️ workflow; kind={submission.kind} start={submission.start} "
        f"end={submission.end}"
    )
    pipeline.run(DataContext(start=submission.start, end=submission.end))
    logs.info(
        f"✅ workflow; kind={submission.kind} start={submission.start} "
        f"end={submission.end}"
    )


def run_standard_fact_bootstrap(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    submission: StandardFactBootstrapSubmission,
) -> None:
    """Materialize only the explicit Standard calendar and fact range.

    Example:
        run_standard_fact_bootstrap(
            app_config=app_config,
            path_manager=path_manager,
            submission=StandardFactBootstrapSubmission(
                start="2019-01-01",
                end="2019-04-03",
            ),
        )
    """
    fact_sources = _standard_fact_sources()
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    adapter_cache: dict[str, BrokerAdapter] = {}
    steps: tuple[PipelineStep[DataContext], ...] = (
        CalendarMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            access=access,
            processed_version=PROCESSED_VERSION,
            adapter_cache=adapter_cache,
        ),
        FactMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            sources=fact_sources,
            broker_classes=BROKER_ADAPTER_CLASSES,
            normalize_operations=_NORMALIZE_OPERATIONS,
            processed_version=PROCESSED_VERSION,
            adapter_cache=adapter_cache,
        ),
    )
    pipeline = DataPipeline(
        steps=steps,
        instrumentation=Instrumentation(
            f"data-standard-bootstrap_{submission.start}_{submission.end}"
        ),
    )
    logs.info(
        f"▶️ workflow; kind=data-standard-bootstrap start={submission.start} "
        f"end={submission.end}"
    )
    pipeline.run(DataContext(start=submission.start, end=submission.end))
    logs.info(
        f"✅ workflow; kind=data-standard-bootstrap start={submission.start} "
        f"end={submission.end}"
    )


def run_feature_backfill(
    *,
    path_manager: PathManager,
    submission: FeatureBackfillSubmission,
) -> None:
    """Backfill one exact Feature identity from committed formal inputs.

    Example:
        run_feature_backfill(
            path_manager=path_manager,
            submission=FeatureBackfillSubmission(
                feature_set="tushare_daily_basic",
                version="v1",
                start="2019-04-04",
                end="2019-07-05",
            ),
        )
    """
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    feature_step = FeatureBuildStep(
        pm=path_manager,
        access=access,
        feature_versions={submission.feature_set: submission.version},
    )
    target_dates = tuple(
        access.trade_dates(
            start_date=submission.start,
            end_date=submission.end,
        )
    )
    pipeline = DataPipeline(
        steps=(feature_step,),
        instrumentation=Instrumentation(
            f"data-feature-backfill_{submission.feature_set}_{submission.version}_"
            f"{submission.start}_{submission.end}"
        ),
    )
    logs.info(
        f"▶️ workflow; kind=data-feature-backfill "
        f"feature_set={submission.feature_set} version={submission.version} "
        f"start={submission.start} end={submission.end} targets={len(target_dates)}"
    )
    pipeline.run(
        DataContext(
            start=submission.start,
            end=submission.end,
            trade_dates=target_dates,
        )
    )
    logs.info(
        f"✅ workflow; kind=data-feature-backfill "
        f"feature_set={submission.feature_set} version={submission.version} "
        f"start={submission.start} end={submission.end} targets={len(target_dates)}"
    )


def run_level2_minute_backfill(
    *,
    path_manager: PathManager,
    submission: Level2MinuteBackfillSubmission,
) -> None:
    """Backfill both Level2 stock minute facts from committed trades.

    Example:
        run_level2_minute_backfill(
            path_manager=path_manager,
            submission=Level2MinuteBackfillSubmission(
                start="2025-11-18",
                end="2025-11-18",
            ),
        )
    """
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    target_dates = tuple(
        access.trade_dates(
            start_date=submission.start,
            end_date=submission.end,
        )
    )
    minute_step = Level2MinuteBuildStep(
        pm=path_manager,
        access=access,
        processed_version=PROCESSED_VERSION,
        symbol_batch_size=_LEVEL2_MINUTE_SYMBOL_BATCH_SIZE,
    )
    pipeline = DataPipeline(
        steps=(minute_step,),
        instrumentation=Instrumentation(
            f"data-level2-minute-backfill_{submission.start}_{submission.end}"
        ),
    )
    logs.info(
        f"▶️ workflow; kind=data-level2-minute-backfill "
        f"start={submission.start} end={submission.end} "
        f"targets={len(target_dates)}"
    )
    pipeline.run(
        DataContext(
            start=submission.start,
            end=submission.end,
            trade_dates=target_dates,
        )
    )
    logs.info(
        f"✅ workflow; kind=data-level2-minute-backfill "
        f"start={submission.start} end={submission.end} "
        f"targets={len(target_dates)}"
    )
