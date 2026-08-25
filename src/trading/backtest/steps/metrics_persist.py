# filepath: src/trading/backtest/steps/metrics_persist.py
"""Persist accumulated daily-alpha backtest metrics."""

from __future__ import annotations

import json

from src import logs
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.engines.backtest_eval import (
    summarize_execution_quality_frames,
    summarize_full_backtest_frames,
    summarize_risk_effect_frames,
    summarize_signal_eval_frames,
    summarize_tradable_alpha_frames,
)
from src.trading.reporting.metrics import build_backtest_metrics
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


def persist_backtest_metrics(
    *,
    state: BacktestState,
    pm: PathManager,
    experiment_name: str,
) -> None:
    """Persist all accumulated layer and replay metrics.

    Example:
        persist_backtest_metrics(
            state=state,
            pm=path_manager,
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
    """
    metrics = build_backtest_metrics(
        state=state.portfolio_state,
        ledger=state.execution_ledger,
        equity=state.equity_curve,
        signal_tape=state.signal_tape,
        target_tape=state.target_tape,
        trade_dates=state.trade_dates,
    )
    metrics.update(
        {
            "signal_eval": summarize_signal_eval_frames(state.signal_eval_frames),
            "tradable_alpha_eval": summarize_tradable_alpha_frames(
                state.tradable_alpha_frames,
            ),
            "risk_eval": summarize_risk_effect_frames(state.risk_decision_frames),
            "execution_eval": summarize_execution_quality_frames(
                state.execution_eval_frames,
            ),
            "full_backtest": summarize_full_backtest_frames(
                state.full_backtest_frames,
            ),
        }
    )
    metrics_path = pm.experiment_backtest_metrics(
        experiment_name=experiment_name,
    )
    FileSystem.write_bytes_atomic(
        metrics_path,
        json.dumps(
            metrics,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8"),
    )
    logs.info(
        f"✅ backtest metrics publish; path={metrics_path} "
        f"dates={len(state.trade_dates)} bars={state.bar_count} "
        f"signals={state.signal_count}"
    )


class MetricsPersistStep:
    """Persist all metrics accumulated in the final backtest Context.

    Example:
        step = MetricsPersistStep(
            pm=path_manager,
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
        persisted_context = step.run(completed_context)
    """

    def __init__(self, *, pm: PathManager, experiment_name: str) -> None:
        """Bind the experiment metrics destination.

        Example:
            step = MetricsPersistStep(
                pm=path_manager,
                experiment_name="backtest_2026-07-01_2026-07-20_run-1",
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name

    def run(self, context: BacktestContext) -> BacktestContext:
        """Persist metrics and preserve the final backtest Context.

        Example:
            persisted_context = step.run(completed_context)
        """
        persist_backtest_metrics(
            state=context.state,
            pm=self._pm,
            experiment_name=self._experiment_name,
        )
        return context
