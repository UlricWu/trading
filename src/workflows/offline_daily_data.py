# filepath: src/workflows/offline_daily_data.py
"""Execute the fixed range-based offline data workflow."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import cast

from src import logs
from src.access import Access
from src.config.app_config import AppConfig
from src.config.data_config import DataConfig, SourceConfig
from src.data_system.brokers.bootstrap import build_broker_registry
from src.data_system.builders.registry import get_label_builder
from src.data_system.context import DataContext
from src.data_system.steps.fact_ingest_step import FactIngestStep
from src.data_system.steps.fact_normalize_step import FactNormalizeStep
from src.data_system.steps.feature_build_step import FeatureBuildStep
from src.data_system.steps.label_build_step import LabelBuildStep
from src.jobs.requests import DataSubmission
from src.observability.instrumentation import Instrumentation
from src.utils.datetime_utils import DateTimeUtils
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
        status = DataRunStatus.SUCCESS
    """

    SUCCESS = "success"
    SKIPPED = "skipped"


def run_offline_data(
    *,
    app_config: AppConfig,
    path_manager: PathManager,
    submission: DataSubmission,
) -> DataRunStatus:
    """Materialize one complete standard or Level-2 data range.

    Example:
        status = run_offline_data(
            app_config=app_config,
            path_manager=path_manager,
            submission=DataSubmission(
                kind="data-standard",
                start="2026-07-01",
                end="2026-07-20",
            ),
        )
    """
    broker_registry = build_broker_registry()
    group = OFFLINE_STANDARD if submission.kind == "data-standard" else OFFLINE_LEVEL2

    enabled_feature_sets = {
        name for name, config in app_config.data.feature_sets.items() if config.enabled
    }
    unknown_feature_sets = enabled_feature_sets - OFFLINE_STANDARD_FEATURE_SETS
    if unknown_feature_sets:
        raise ValueError(
            f"unsupported enabled feature sets: {sorted(unknown_feature_sets)}"
        )

    enabled_label_sets = {
        name for name, config in app_config.data.label_sets.items() if config.enabled
    }
    unknown_label_sets = enabled_label_sets - OFFLINE_STANDARD_LABEL_SETS
    if unknown_label_sets:
        raise ValueError(
            f"unsupported enabled label sets: {sorted(unknown_label_sets)}"
        )

    selected_sources: dict[str, SourceConfig] = {}
    calendar_source: SourceConfig | None = None
    for source_name, source_config in app_config.data.sources.items():
        is_target_group = source_config.group == group
        can_provide_calendar = source_config.group == OFFLINE_STANDARD
        if not is_target_group and not can_provide_calendar:
            continue
        if not source_config.enabled:
            if is_target_group:
                logs.warning(
                    f"[OfflineData] skip source={source_name} group={group} "
                    f"broker={source_config.broker} reason=source_disabled"
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
                        group=source_config.group,
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
            if can_provide_calendar and effective_name == "trade_calendar":
                if calendar_source is not None:
                    raise ValueError(
                        "offline data produced duplicate trade_calendar source"
                    )
                calendar_source = effective_config

            if not is_target_group:
                continue
            if effective_name in selected_sources:
                raise ValueError(
                    "offline data group produced duplicate effective source "
                    f"name: {effective_name!r}"
                )
            selected_sources[effective_name] = effective_config

    if calendar_source is None:
        raise ValueError("offline data requires the Tushare trade_calendar source")
    if (
        calendar_source.broker != "tushare"
        or calendar_source.raw_object != "trade_calendar"
        or calendar_source.outputs != ["trade_calendar"]
    ):
        raise ValueError(
            "trade_calendar source must map Tushare trade_calendar to "
            "processed trade_calendar"
        )

    fact_sources = {
        name: config
        for name, config in selected_sources.items()
        if name != "trade_calendar"
    }
    if not fact_sources:
        raise ValueError(f"offline data group '{group}' has no fact sources")

    calendar_data = DataConfig(
        brokers=app_config.data.brokers,
        sources={"trade_calendar": calendar_source},
    )
    calendar_config = app_config.model_copy(
        update={"data": calendar_data},
        deep=True,
    )
    selected_data = DataConfig(
        brokers=app_config.data.brokers,
        sources=fact_sources,
        feature_sets=(
            {
                name: config
                for name, config in app_config.data.feature_sets.items()
                if config.enabled
            }
            if submission.kind == "data-standard"
            else {}
        ),
        label_sets=(
            {
                name: config
                for name, config in app_config.data.label_sets.items()
                if config.enabled
            }
            if submission.kind == "data-standard"
            else {}
        ),
    )
    selected_config = app_config.model_copy(
        update={"data": selected_data},
        deep=True,
    )

    natural_dates = DateTimeUtils.date_range(submission.start, submission.end)
    calendar_ingest = FactIngestStep(
        app_cfg=calendar_config,
        broker_registry=broker_registry,
    )
    calendar_normalize = FactNormalizeStep(app_cfg=calendar_config)
    fact_ingest = FactIngestStep(
        app_cfg=selected_config,
        broker_registry=broker_registry,
    )
    fact_normalize = FactNormalizeStep(app_cfg=selected_config)

    logs.info(
        f"[OfflineData] started kind={submission.kind} "
        f"start={submission.start} end={submission.end}"
    )
    scope_name = f"{submission.kind}_{submission.start}_{submission.end}"
    with Instrumentation(scope_name) as instrumentation:
        for natural_date in natural_dates:
            context = DataContext(
                trade_date=natural_date,
                pm=path_manager,
            )
            if not instrumentation.call(calendar_ingest, context):
                raise RuntimeError(
                    f"[OfflineData] missing trade_calendar trade_date={natural_date}"
                )
            instrumentation.call(calendar_normalize, context)

        calendar_version = cast(
            str,
            calendar_config.data.brokers[calendar_source.broker].normalize_profile,
        )
        access = Access(
            pm=path_manager,
            processed_version=calendar_version,
        )
        open_dates = access.trade_dates(
            start_date=submission.start,
            end_date=submission.end,
        )

        available_fact_dates: list[str] = []
        missing_fact_dates: list[str] = []
        for trade_date in open_dates:
            context = DataContext(
                trade_date=trade_date,
                pm=path_manager,
            )
            if not instrumentation.call(fact_ingest, context):
                missing_fact_dates.append(trade_date)
                continue
            instrumentation.call(fact_normalize, context)
            available_fact_dates.append(trade_date)

        if missing_fact_dates:
            if submission.kind == "data-standard" or available_fact_dates:
                raise RuntimeError(
                    "[OfflineData] fact dates are only partially available "
                    f"missing_dates={missing_fact_dates}"
                )
            logs.warning(
                f"[OfflineData] skipped kind={submission.kind} "
                f"start={submission.start} end={submission.end} "
                "reason=no_source_payload"
            )
            return DataRunStatus.SKIPPED

        if submission.kind == "data-standard":
            feature_step = FeatureBuildStep(app_cfg=selected_config)
            for maturity_date in open_dates:
                if selected_config.data.feature_sets:
                    instrumentation.call(
                        feature_step,
                        DataContext(
                            trade_date=maturity_date,
                            pm=path_manager,
                        ),
                    )

                for label_set, label_config in selected_config.data.label_sets.items():
                    builder = get_label_builder(
                        label_set,
                        label_config.version,
                    )
                    lookahead = max(
                        builder.target_lookahead(column)
                        for column in builder.output_columns
                    )
                    input_dates = tuple(
                        access.recent_trade_dates(
                            end_date=maturity_date,
                            sessions=lookahead + 1,
                        )
                    )
                    instrumentation.call(
                        LabelBuildStep(
                            label_set=label_set,
                            version=label_config.version,
                            builder=builder,
                            input_dates=input_dates,
                        ),
                        DataContext(
                            trade_date=input_dates[0],
                            pm=path_manager,
                        ),
                    )

    logs.info(
        f"[OfflineData] finished kind={submission.kind} "
        f"start={submission.start} end={submission.end}"
    )
    return DataRunStatus.SUCCESS
