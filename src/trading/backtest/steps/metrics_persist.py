# filepath: src/trading/backtest/steps/metrics_persist.py
from __future__ import annotations

import json

from src import logs
from src.trading.backtest.context import TradingContext
from src.trading.engines.backtest_eval import (
    summarize_execution_quality_frames,
    summarize_full_backtest_frames,
    summarize_risk_effect_frames,
    summarize_signal_eval_frames,
    summarize_tradable_alpha_frames,
)
from src.trading.reporting.metrics import build_backtest_metrics
from src.utils.filesystem import FileSystem


class MetricsPersistStep:
    """Persist accumulated replay metrics as backtest artifacts.

    Example:
        step = MetricsPersistStep()
        step(context)
    """

    def __call__(self, ctx: TradingContext) -> None:
        """Write the final backtest metrics artifact.

        Example:
            step(context)
        """
        state = ctx.portfolio_state
        ledger = ctx.execution_ledger
        equity = ctx.equity_curve
        signal_tape = ctx.signal_tape
        target_tape = ctx.target_tape

        metrics = build_backtest_metrics(
            state=state,
            ledger=ledger,
            equity=equity,
            signal_tape=signal_tape,
            target_tape=target_tape,
            trade_dates=ctx.trade_dates,
        )
        metrics.update(
            {
                "signal_eval": summarize_signal_eval_frames(ctx.signal_eval_frames),
                "tradable_alpha_eval": summarize_tradable_alpha_frames(
                    ctx.tradable_alpha_frames,
                ),
                "risk_eval": summarize_risk_effect_frames(ctx.risk_decision_frames),
                "execution_eval": summarize_execution_quality_frames(
                    ctx.execution_eval_frames,
                ),
                "full_backtest": summarize_full_backtest_frames(
                    ctx.full_backtest_frames,
                ),
            }
        )
        metrics_path = ctx.pm.experiment_backtest_metrics(
            experiment_name=ctx.experiment_name,
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
            f"[MetricsPersistStep] saved metrics={metrics_path} "
            f"dates={len(ctx.trade_dates)} bars={ctx.bar_count} "
            f"signals={ctx.signal_count}"
        )
