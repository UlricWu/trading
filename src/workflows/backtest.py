# filepath: src/workflows/backtest.py
"""daily_alpha `/jobs backtest` workflow."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.access import Access
from src.config.backtest_config import BacktestConfig
from src.observability.instrumentation import Instrumentation
from src.pipeline.step import PipelineStep
from src.trading.backtest.timing import BacktestTiming, resolve_backtest_timing
from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.pipeline.context import TradingContext
from src.trading.pipeline.pipeline import TradingPipeline
from src.trading.pipeline.steps.backtest_layers import (
    AccountingStep,
    ExecutionEvalStep,
    FullBacktestStep,
    PortfolioStep,
    RiskEvalStep,
    SignalEvalStep,
    SignalStep,
    TradableAlphaEvalStep,
)
from src.trading.pipeline.steps.metrics_persist import MetricsPersistStep
from src.trading.pipeline.steps.report import ReportStep
from src.trading.portfolio.state import PortfolioState
from src.trading.sim import components as sim_components
from src.utils.path import PathManager


class TradeCalendar(Protocol):
    """Provide available backtest dates for one requested range."""

    def __call__(
        self,
        *,
        pm: PathManager,
        start_date: str,
        end_date: str,
    ) -> Sequence[str]: ...


class TimingResolver(Protocol):
    """Resolve ordered open dates into backtest timing rows."""

    def __call__(
        self,
        *,
        open_dates: Sequence[str],
    ) -> list[BacktestTiming]: ...


def build_backtest_experiment_name(
    *,
    start_date: str,
    end_date: str,
    experiment_id: str,
) -> str:
    """Return the `/jobs backtest` artifact namespace for one accepted range."""
    return f"backtest_{start_date}_{end_date}_{experiment_id}"


def build_backtest_schedule(
    *,
    path_manager: PathManager,
    start_date: str,
    end_date: str,
    calendar_fn: TradeCalendar | None = None,
    timing_fn: TimingResolver = resolve_backtest_timing,
) -> list[BacktestTiming]:
    """Resolve one daily_alpha date range into pipeline timing rows."""
    open_dates: Sequence[str]
    if calendar_fn is None:
        open_dates = Access(
            pm=path_manager,
            processed_version="v1",
        ).trade_dates(
            start_date=start_date,
            end_date=end_date,
        )
    else:
        open_dates = calendar_fn(
            pm=path_manager,
            start_date=start_date,
            end_date=end_date,
        )
    return timing_fn(open_dates=open_dates)


def run_daily_alpha_backtest(
    *,
    backtest_config: BacktestConfig,
    path_manager: PathManager,
    experiment_id: str,
    start_date: str,
    end_date: str,
) -> TradingContext:
    """
    Run one `/jobs backtest` daily_alpha range through the configured mode.

    The CLI has already written its overrides into ``backtest_config``. The
    workflow therefore reads one authoritative config and derives the
    experiment namespace from the accepted job identity.
    """
    instrumentation = Instrumentation()
    experiment_name = build_backtest_experiment_name(
        start_date=start_date,
        end_date=end_date,
        experiment_id=experiment_id,
    )
    experiment_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    if experiment_dir.exists():
        raise FileExistsError(f"experiment already exists: {experiment_name}")

    backtest_timings = build_backtest_schedule(
        path_manager=path_manager,
        start_date=start_date,
        end_date=end_date,
    )

    backtest_components = sim_components.build_components(
        mode=backtest_config.backtest_mode,
        cfg=backtest_config,
        pm=path_manager,
    )

    trading_context = TradingContext(
        pm=path_manager,
        cfg=backtest_config,
        experiment_name=experiment_name,
        portfolio_state=PortfolioState(
            initial_cash=float(backtest_config.init_cash),
            cash=float(backtest_config.init_cash),
        ),
        execution_ledger=ExecutionLedger(),
        equity_curve=EquityCurve(),
        signal_tape=SignalTape(),
        target_tape=TargetTape(),
    )

    per_timing_steps: list[PipelineStep[TradingContext]] = [
        SignalStep(
            signal=backtest_components.signal,
            feature_set=backtest_components.feature_set,
            feature_version=backtest_components.feature_version,
            feature_names=backtest_components.feature_names,
        ),
        SignalEvalStep(),
        TradableAlphaEvalStep(),
        PortfolioStep(
            constructor=backtest_components.constructor,
            target_capacity=backtest_components.target_capacity,
        ),
        RiskEvalStep(risk=backtest_components.risk),
        ExecutionEvalStep(execution=backtest_components.execution),
        AccountingStep(),
        FullBacktestStep(),
    ]
    final_steps: list[PipelineStep[TradingContext]] = [
        MetricsPersistStep(),
        ReportStep(),
    ]

    return TradingPipeline(
        backtest_timings=backtest_timings,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        inst=instrumentation,
    ).run(trading_context)
