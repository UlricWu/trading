# filepath: src/trading/backtest/steps/metrics_persist.py
from __future__ import annotations

import json

from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


def persist_backtest_metrics(*, metrics: BacktestMetrics, pm: PathManager, experiment_name: str) -> None:
    """Write actual evaluations, preserving undefined numeric values and reasons.

    Example:
        persist_backtest_metrics(metrics=metrics, pm=pm, experiment_name=experiment_name)
    """
    payload = metrics.model_dump(mode="json")
    for name in ("signal_eval", "risk_eval", "execution_eval", "portfolio", "final_account"):
        if payload[name] is None:
            del payload[name]
    FileSystem.write_bytes_atomic(pm.experiment_backtest_metrics(experiment_name=experiment_name),
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode())
