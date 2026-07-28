# filepath: src/trading/backtest/context.py
"""Context carrier for the current daily_alpha backtest workflow."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config.backtest_config import BacktestConfig
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.engines.backtest_eval import (
    ExecutionQualityFrame,
    FullBacktestFrame,
    RiskEffectFrame,
    SignalEvalFrame,
    TradableAlphaFrame,
)
from src.trading.portfolio.state import PortfolioState
from src.trading.sim.kernel import BarContext
from src.utils.path import PathManager


@dataclass(slots=True)
class TradingContext:
    """Carry the mutable state of one daily-alpha backtest.

    Example:
        context = TradingContext(
            pm=path_manager,
            cfg=backtest_config,
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
            portfolio_state=portfolio_state,
            execution_ledger=execution_ledger,
            equity_curve=equity_curve,
            signal_tape=signal_tape,
            target_tape=target_tape,
        )
    """

    pm: PathManager
    cfg: BacktestConfig
    experiment_name: str
    portfolio_state: PortfolioState
    execution_ledger: ExecutionLedger
    equity_curve: EquityCurve
    signal_tape: SignalTape
    target_tape: TargetTape
    backtest_timing: BacktestTiming = field(init=False, repr=False)
    current_bar: BarContext = field(init=False, repr=False)
    current_bars_count: int = 0
    current_raw_prices: dict[str, float] = field(default_factory=dict)
    current_forward_raw_prices: dict[str, float] = field(default_factory=dict)
    current_scores: dict[str, float] = field(default_factory=dict)
    current_raw_targets: dict[str, int] = field(default_factory=dict)
    current_targets: dict[str, int] = field(default_factory=dict)
    signal_eval_frames: list[SignalEvalFrame] = field(default_factory=list)
    tradable_alpha_frames: list[TradableAlphaFrame] = field(default_factory=list)
    risk_decision_frames: list[RiskEffectFrame] = field(default_factory=list)
    execution_eval_frames: list[ExecutionQualityFrame] = field(default_factory=list)
    full_backtest_frames: list[FullBacktestFrame] = field(default_factory=list)
    bar_count: int = 0
    signal_count: int = 0
    last_mark_prices: dict[str, float] = field(default_factory=dict)
    trade_dates: list[str] = field(default_factory=list)
