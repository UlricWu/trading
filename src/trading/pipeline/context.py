# filepath: src/trading/pipeline/context.py
"""Context carrier for the current daily_alpha backtest pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.trading.sim.kernel import BarContext
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.equity import EquityCurve
from src.trading.core.events import FillEvent
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
from src.utils.path import PathManager
from src.config.backtest_config import BacktestConfig


@dataclass(slots=True)
class TradingContext:
    """
    Runtime carrier for the current daily_alpha backtest pipeline.

    Core replay objects are constructed before the pipeline starts and are
    always usable by steps. The current `backtest_timing` is assigned by
    `TradingPipeline` immediately before each per-timing step runs.
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
    current_bar: BarContext | None = field(default=None, repr=False)
    current_bars_count: int = 0
    current_raw_prices: dict[str, float] = field(default_factory=dict)
    current_forward_raw_prices: dict[str, float] = field(default_factory=dict)
    current_signal_symbols: list[str] = field(default_factory=list)
    current_scores: dict[str, float] = field(default_factory=dict)
    current_raw_targets: dict[str, int] = field(default_factory=dict)
    current_targets: dict[str, int] = field(default_factory=dict)
    current_fills: list[FillEvent] = field(default_factory=list)
    signal_eval_frames: list[SignalEvalFrame] = field(default_factory=list)
    tradable_alpha_frames: list[TradableAlphaFrame] = field(default_factory=list)
    risk_decision_frames: list[RiskEffectFrame] = field(default_factory=list)
    execution_eval_frames: list[ExecutionQualityFrame] = field(default_factory=list)
    full_backtest_frames: list[FullBacktestFrame] = field(default_factory=list)
    bar_count: int = 0
    signal_count: int = 0
    last_mark_prices: dict[str, float] = field(default_factory=dict)
    trade_dates: list[str] = field(default_factory=list)

    def require_current_bar(self) -> BarContext:
        """Return the active replay bar or reject an invalid step order."""
        if self.current_bar is None:
            raise RuntimeError("current backtest bar is not initialized")
        return self.current_bar
