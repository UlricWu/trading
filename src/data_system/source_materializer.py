# filepath: src/data_system/source_materializer.py
"""Materialize one bound source set from raw ingest through normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter, DownloadPlan
from src.data_system.normalize.profiles import NormalizeOutput
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager

LEVEL2_TRADE_OUTPUTS = frozenset({"sh_trade", "sz_trade"})


@dataclass(frozen=True, slots=True)
class _NormalizePlan:
    source_name: str
    source: SourceConfig
    output: str
    profile: Callable[..., NormalizeOutput]


class SourceMaterializer:
    """Bind and materialize one selected source set completely per date.

    Example:
        materializer = SourceMaterializer(
            app_config=app_config,
            path_manager=path_manager,
            sources=selected_sources,
            broker_classes=broker_classes,
            normalize_profiles=normalize_profiles,
            processed_version="v1",
            adapter_cache={},
        )
        available = materializer.materialize("2026-07-20")
    """

    def __init__(
        self,
        *,
        app_config: AppConfig,
        path_manager: PathManager,
        sources: Mapping[str, SourceConfig],
        broker_classes: Mapping[str, type[BrokerAdapter]],
        normalize_profiles: Mapping[str, Callable[..., NormalizeOutput]],
        processed_version: str,
        adapter_cache: dict[str, BrokerAdapter],
    ) -> None:
        """Resolve source capabilities before any date I/O.

        Example:
            materializer = SourceMaterializer(
                app_config=app_config,
                path_manager=path_manager,
                sources=selected_sources,
                broker_classes=broker_classes,
                normalize_profiles=normalize_profiles,
                processed_version="v1",
                adapter_cache={},
            )
        """
        if not sources:
            raise ValueError("SourceMaterializer requires at least one source")
        self._app_config = app_config
        self._path_manager = path_manager
        self._sources = dict(sources)
        self._broker_classes = broker_classes
        self._adapter_cache = adapter_cache
        self._processed_version = processed_version

        plans: list[_NormalizePlan] = []
        for source_name, source in self._sources.items():
            if source.broker not in broker_classes:
                raise KeyError(f"Broker '{source.broker}' is not registered")
            outputs = source.outputs or ()
            if not outputs:
                continue
            try:
                profile = normalize_profiles[source.broker]
            except KeyError as exc:
                raise KeyError(
                    f"normalize profile is not registered for broker '{source.broker}'"
                ) from exc
            plans.extend(
                _NormalizePlan(
                    source_name=source_name,
                    source=source,
                    output=output,
                    profile=profile,
                )
                for output in outputs
            )
        self._normalize_plans = tuple(plans)

    def materialize(self, trade_date: str) -> bool:
        """Ingest one date and normalize it only when every source is available.

        Example:
            available = materializer.materialize("2026-07-20")
        """
        if not self._ingest(trade_date):
            return False
        self._normalize(trade_date)
        return True

    def _ingest(self, trade_date: str) -> bool:
        logs.info(f"[FactIngest] start DATE={trade_date}")
        available_payload = False
        missing_payloads = 0

        for source_name, source in self._sources.items():
            plan = DownloadPlan(
                source_name=source_name,
                trade_date=trade_date,
                broker=source.broker,
                raw_object=source.raw_object,
            )
            meta_path = self._path_manager.raw_meta(
                broker=plan.broker,
                trade_date=plan.trade_date,
                source_name=plan.source_name,
            )
            if meta.find(pm=self._path_manager, meta_path=meta_path) is not None:
                available_payload = True
                logs.warning(
                    f"[FactIngest] meta hit → skip fact={plan.source_name} "
                    f"output={meta_path}"
                )
                continue

            adapter = self._adapter_cache.get(plan.broker)
            if adapter is None:
                adapter = self._broker_classes[plan.broker](app_cfg=self._app_config)
                self._adapter_cache[plan.broker] = adapter
            logs.info(f"[FactIngest] downloading plan={plan}")
            fetched = adapter.fetch(record=plan, pm=self._path_manager)
            if fetched is None:
                logs.warning(
                    f"[FactIngest] no source data trade_date={trade_date} "
                    f"source={plan.source_name} broker={plan.broker}"
                )
                missing_payloads += 1
                continue

            output_file = self._path_manager.raw_payload(
                broker=fetched.broker,
                trade_date=fetched.trade_date,
                source_name=fetched.source_name,
                payload_file=fetched.payload_file,
            )
            meta.commit(pm=self._path_manager, payload_path=output_file)
            available_payload = True

        logs.info(f"[FactIngest] finished trade_date={trade_date}")
        if not available_payload:
            return False
        if missing_payloads:
            raise RuntimeError("source payloads are only partially available")
        return True

    def _normalize(self, trade_date: str) -> None:
        logs.info(f"[FactNormalize] start DATE={trade_date}")
        for plan in self._normalize_plans:
            processed_meta = self._path_manager.processed_meta(
                dataset_name=plan.output,
                version=self._processed_version,
                trade_date=trade_date,
            )
            output_file = self._path_manager.processed_data(
                dataset_name=plan.output,
                version=self._processed_version,
                trade_date=trade_date,
            )
            loaded_output = meta.find(
                pm=self._path_manager,
                meta_path=processed_meta,
                expected_payload_path=output_file,
            )
            if (
                plan.output in LEVEL2_TRADE_OUTPUTS
                and loaded_output is not None
                and loaded_output.symbol_slices is None
            ):
                raise RuntimeError(
                    "Level-2 Meta has no symbol_slices: "
                    f"dataset={plan.output}, meta_path={processed_meta}"
                )
            if loaded_output is not None:
                logs.info(
                    "[FactNormalize] meta hit -> skip "
                    f"target={plan.output} source={plan.source_name}"
                )
                continue

            raw_meta_path = self._path_manager.raw_meta(
                broker=plan.source.broker,
                source_name=plan.source_name,
                trade_date=trade_date,
            )
            loaded_input = meta.require(
                pm=self._path_manager,
                meta_path=raw_meta_path,
            )
            staging_candidate = self._path_manager.staging_payload(
                broker=plan.source.broker,
                source_name=plan.source_name,
                trade_date=trade_date,
                payload_file=loaded_input.payload_path.name,
            )
            input_file = loaded_input.payload_path
            if (
                staging_candidate.is_file()
                and staging_candidate.stat().st_size == loaded_input.size_bytes
            ):
                input_file = staging_candidate

            logs.info(f"[FactNormalize] normalizing profile={plan.profile}")
            target = plan.profile(
                input_file=input_file,
                raw_object=plan.source.raw_object,
                target_name=plan.output,
                output_name=output_file,
                trade_date=trade_date,
            )
            table_ops.require_nonempty(
                target.table,
                who=(
                    f"FactNormalize source={plan.source_name} "
                    f"target={plan.output} trade_date={trade_date}"
                ),
            )
            write_parquet_atomic(output_file=output_file, table=target.table)
            meta.commit(
                pm=self._path_manager,
                payload_path=output_file,
                upstream_meta_path=raw_meta_path,
                symbol_slices=target.symbol_slices,
            )
        logs.info(f"[FactNormalize] finished trade_date={trade_date}")
