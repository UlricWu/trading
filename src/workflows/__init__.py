# filepath: src/workflows/__init__.py
"""Shared offline workflow identities and broker lifetimes."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Literal, cast

from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter
from src.utils.path import PathManager

PROCESSED_VERSION = "v1"


def _get_broker[BrokerT: BrokerAdapter](
    *,
    app_config: AppConfig,
    broker_class: type[BrokerT],
    adapter_cache: MutableMapping[str, BrokerAdapter],
) -> BrokerT:
    """Reuse the workflow's single class and configuration binding per broker name."""
    broker = adapter_cache.get(broker_class.name)
    if broker is None:
        broker = broker_class(app_cfg=app_config)
        adapter_cache[broker_class.name] = broker
    return cast(BrokerT, broker)


def require_new_experiment(
    *,
    path_manager: PathManager,
    kind: Literal["training", "backtest"],
    start_date: str,
    end_date: str,
    experiment_id: str,
) -> str:
    """Return a non-existing formal experiment name without reserving it.

    Example:
        experiment_name = require_new_experiment(
            path_manager=path_manager,
            kind="training",
            start_date="2026-07-01",
            end_date="2026-07-20",
            experiment_id="run-1",
        )
    """
    experiment_name = f"{kind}_{start_date}_{end_date}_{experiment_id}"
    if path_manager.experiment_dir(experiment_name=experiment_name).exists():
        raise FileExistsError(f"experiment already exists: {experiment_name}")
    return experiment_name
