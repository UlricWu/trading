# filepath: src/workflows/__init__.py
"""Shared identities owned by the offline workflow contract."""

from __future__ import annotations

from typing import Literal

from src.utils.path import PathManager

PROCESSED_VERSION = "v1"


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
