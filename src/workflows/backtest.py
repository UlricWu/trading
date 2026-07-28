# filepath: src/workflows/backtest.py
"""daily_alpha `/jobs backtest` workflow."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from src.access import Access
from src.config.backtest_config import BacktestConfig
from src.observability.instrumentation import Instrumentation
from src.pipeline import run_steps
from src.trading.backtest.context import TradingContext
from src.trading.backtest.steps.backtest_layers import (
    AccountingStep,
    ExecutionEvalStep,
    FullBacktestStep,
    PortfolioStep,
    RiskEvalStep,
    SignalEvalStep,
    SignalStep,
    TradableAlphaEvalStep,
)
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.backtest.timing import BacktestTiming, resolve_backtest_timing
from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.portfolio.state import PortfolioState
from src.trading.sim import components as sim_components
from src.utils.path import PathManager


class TradeCalendar(Protocol):
    """Provide available backtest dates for one requested range.

    Example:
        dates = calendar(
            pm=path_manager,
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """

    def __call__(
        self,
        *,
        pm: PathManager,
        start_date: str,
        end_date: str,
    ) -> Sequence[str]:
        """Return ordered open dates in the requested range.

        Example:
            dates = calendar(
                pm=path_manager,
                start_date="2026-07-01",
                end_date="2026-07-20",
            )
        """
        ...


class TimingResolver(Protocol):
    """Resolve ordered open dates into backtest timing rows.

    Example:
        timings = timing_resolver(
            open_dates=("2026-07-20", "2026-07-21"),
        )
    """

    def __call__(
        self,
        *,
        open_dates: Sequence[str],
    ) -> list[BacktestTiming]:
        """Return timing rows for ordered open dates.

        Example:
            timings = timing_resolver(
                open_dates=("2026-07-20", "2026-07-21"),
            )
        """
        ...


def build_backtest_experiment_name(
    *,
    start_date: str,
    end_date: str,
    experiment_id: str,
) -> str:
    """Return the `/jobs backtest` artifact namespace for one accepted range.

    Example:
        name = build_backtest_experiment_name(
            start_date="2026-07-01",
            end_date="2026-07-20",
            experiment_id="run-1",
        )
    """
    return f"backtest_{start_date}_{end_date}_{experiment_id}"


def build_backtest_schedule(
    *,
    path_manager: PathManager,
    start_date: str,
    end_date: str,
    calendar_fn: TradeCalendar | None = None,
    timing_fn: TimingResolver = resolve_backtest_timing,
) -> list[BacktestTiming]:
    """Resolve one daily-alpha date range into runnable timing rows.

    Example:
        timings = build_backtest_schedule(
            path_manager=path_manager,
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """
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
    timings = timing_fn(open_dates=open_dates)
    if not timings:
        raise RuntimeError("[BacktestWorkflow] backtest_timings is required")
    return timings


def run_daily_alpha_backtest(
    *,
    backtest_config: BacktestConfig,
    path_manager: PathManager,
    experiment_id: str,
    start_date: str,
    end_date: str,
) -> None:
    """Run one `/jobs backtest` daily-alpha range.

    The CLI has already written its overrides into ``backtest_config``. The
    workflow therefore reads one authoritative config and derives the
    experiment namespace from the accepted job identity.

    Example:
        run_daily_alpha_backtest(
            backtest_config=backtest_config,
            path_manager=path_manager,
            experiment_id="run-1",
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """
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

    per_timing_steps: tuple[Callable[[TradingContext], None], ...] = (
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
    )
    final_steps: tuple[Callable[[TradingContext], None], ...] = (
        MetricsPersistStep(),
        ReportStep(),
    )

    with Instrumentation(experiment_name) as instrumentation:
        for timing in backtest_timings:
            trading_context.backtest_timing = timing
            run_steps(trading_context, per_timing_steps, instrumentation)
        run_steps(trading_context, final_steps, instrumentation)
