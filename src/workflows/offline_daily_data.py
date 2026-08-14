# filepath: src/workflows/offline_daily_data.py
"""Execute the fixed range-based offline data workflow."""

from __future__ import annotations

from src import logs
from src.access import Access
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.brokers.bootstrap import BROKER_ADAPTER_CLASSES
from src.data_system.brokers.tushare import TushareBroker
from src.data_system.context import DataContext
from src.data_system.normalize.profiles import NORMALIZE_PROFILES
from src.data_system.pipeline import DataPipeline
from src.data_system.source_materializer import SourceMaterializer
from src.data_system.steps.calendar_materialize_step import CalendarMaterializeStep
from src.data_system.steps.fact_materialize_step import FactMaterializeStep
from src.data_system.steps.feature_build_step import FeatureBuildStep
from src.data_system.steps.label_build_step import LabelBuildStep
from src.jobs.requests import DataSubmission
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION

OFFLINE_STANDARD = "offline_standard"
OFFLINE_LEVEL2 = "offline_level2"


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
    calendar_source =  "trade_calendar"
    tushare_sources = {
        source_name: SourceConfig(
            enabled=True,
            broker=TushareBroker.name,
            group=OFFLINE_STANDARD,
            raw_object=source_name,
            outputs=[source_name],
        )
        for source_name in TushareBroker.active_source_names() if source_name !=calendar_source
    }

    if submission.kind == "data-standard":
        fact_sources = tushare_sources
        feature_sets = {
            name: config
            for name, config in app_config.data.feature_sets.items()
            if config.enabled
        }
        label_sets = {
            name: config
            for name, config in app_config.data.label_sets.items()
            if config.enabled
        }
    else:
        fact_sources = {}
        for source_name, source_config in app_config.data.sources.items():
            if not source_config.enabled:
                logs.warning(
                    f"[OfflineData] skip source={source_name} group={OFFLINE_LEVEL2} "
                    f"broker={source_config.broker} reason=source_disabled"
                )
                continue
            fact_sources[source_name] = source_config
        feature_sets = {}
        label_sets = {}

    if not fact_sources:
        raise ValueError(f"offline data kind '{submission.kind}' has no fact sources")

    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    adapter_cache: dict[str, BrokerAdapter] = {}
    calendar_materializer = SourceMaterializer(
        app_config=app_config,
        path_manager=path_manager,
        sources={"trade_calendar": calendar_source},
        broker_classes=BROKER_ADAPTER_CLASSES,
        normalize_profiles=NORMALIZE_PROFILES,
        processed_version=PROCESSED_VERSION,
        adapter_cache=adapter_cache,
    )
    fact_materializer = SourceMaterializer(
        app_config=app_config,
        path_manager=path_manager,
        sources=fact_sources,
        broker_classes=BROKER_ADAPTER_CLASSES,
        normalize_profiles=NORMALIZE_PROFILES,
        processed_version=PROCESSED_VERSION,
        adapter_cache=adapter_cache,
    )
    steps: tuple[PipelineStep[DataContext], ...] = (
        CalendarMaterializeStep(
            materializer=calendar_materializer,
            access=access,
        ),
        FactMaterializeStep(materializer=fact_materializer),
        FeatureBuildStep(
            pm=path_manager,
            access=access,
            processed_version=PROCESSED_VERSION,
            feature_sets=feature_sets,
        ),
        LabelBuildStep(
            pm=path_manager,
            access=access,
            processed_version=PROCESSED_VERSION,
            label_sets=label_sets,
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
        f"[OfflineData] started kind={submission.kind} "
        f"start={submission.start} end={submission.end}"
    )
    pipeline.run(DataContext(start=submission.start, end=submission.end))
    logs.info(
        f"[OfflineData] finished kind={submission.kind} "
        f"start={submission.start} end={submission.end}"
    )
