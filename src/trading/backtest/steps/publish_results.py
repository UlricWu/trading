# filepath: src/trading/backtest/steps/publish_results.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.trading.backtest.context import BacktestContext
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.utils.filesystem import FileSystem


@dataclass(frozen=True, slots=True)
class PublishResultsStep:
    """Persist metrics, then render the report and summary from that saved result.

    Example:
        context = PublishResultsStep(output_dir=output_dir).run(context)
    """

    output_dir: Path

    def run(self, context: BacktestContext) -> BacktestContext:
        """Publish each file atomically and propagate report failures as task failures.

        Example:
            context = step.run(context)
        """
        payload = context.metrics.model_dump(mode="json")
        for name in ("signal_eval", "risk_eval", "execution_eval", "portfolio", "final_account", "risk_effectiveness", "corporate_actions"):
            if payload[name] is None:
                del payload[name]
        FileSystem.write_bytes_atomic(self.output_dir / "metrics.json",
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode())
        ReportingBundle(self.output_dir).run()
        return context
