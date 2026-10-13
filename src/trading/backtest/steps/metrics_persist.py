# filepath: src/trading/backtest/steps/metrics_persist.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.trading.backtest.context import BacktestResultContext
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.utils.filesystem import FileSystem


def persist_backtest_metrics(*, metrics: BacktestMetrics, output_dir: Path) -> None:
    """Write actual evaluations, preserving undefined numeric values and reasons.

    Example:
        persist_backtest_metrics(metrics=metrics, output_dir=output_dir)
    """
    payload = metrics.model_dump(mode="json")
    for name in ("signal_eval", "risk_eval", "execution_eval", "portfolio", "final_account", "risk_effectiveness"):
        if payload[name] is None:
            del payload[name]
    FileSystem.write_bytes_atomic(output_dir / "metrics.json",
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode())


@dataclass(frozen=True, slots=True)
class MetricsPersistStep:
    """Publish completed evaluations before allowing report generation.

    Example:
        context = MetricsPersistStep(output_dir).run(context)
    """

    output_dir: Path

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Atomically persist the current results and propagate write failures.

        Example:
            context = step.run(context)
        """
        persist_backtest_metrics(metrics=context.metrics, output_dir=self.output_dir)
        return context
