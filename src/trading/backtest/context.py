# filepath: src/trading/backtest/context.py
"""Persistent state and per-timing Context for the daily-alpha backtest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from src.trading.backtest.steps.backtest_layers import (
        PortfolioResult,
        RiskResult,
        SignalEvaluationResult,
        SignalResult,
    )
    from src.trading.backtest.timing import BacktestTiming


@dataclass(slots=True)
class BacktestState:
    """Carry only values that persist across backtest timings.

    Example:
        state = BacktestState.initial(initial_cash=200_000)
        cash = state.portfolio_state.cash
    """

    portfolio_state: PortfolioState
    execution_ledger: ExecutionLedger = field(default_factory=ExecutionLedger)
    equity_curve: EquityCurve = field(default_factory=EquityCurve)
    signal_tape: SignalTape = field(default_factory=SignalTape)
    target_tape: TargetTape = field(default_factory=TargetTape)
    signal_eval_frames: list[SignalEvalFrame] = field(default_factory=list)
    tradable_alpha_frames: list[TradableAlphaFrame] = field(default_factory=list)
    risk_decision_frames: list[RiskEffectFrame] = field(default_factory=list)
    execution_eval_frames: list[ExecutionQualityFrame] = field(default_factory=list)
    full_backtest_frames: list[FullBacktestFrame] = field(default_factory=list)
    bar_count: int = 0
    signal_count: int = 0
    last_mark_prices: dict[str, float] = field(default_factory=dict)
    trade_dates: list[str] = field(default_factory=list)

    @classmethod
    def initial(cls, *, initial_cash: int) -> BacktestState:
        """Create a fully initialized state for one backtest.

        Example:
            state = BacktestState.initial(initial_cash=200_000)
        """
        cash = float(initial_cash)
        return cls(
            portfolio_state=PortfolioState(
                initial_cash=cash,
                cash=cash,
            )
        )


@dataclass(slots=True)
class BacktestContext:
    """Carry one timing's transient values and shared persistent state.

    Example:
        context = BacktestContext(
            timing=timing,
            state=BacktestState.initial(initial_cash=200_000),
        )
    """

    timing: BacktestTiming
    state: BacktestState
    signal: SignalResult | None = None
    signal_evaluation: SignalEvaluationResult | None = None
    portfolio: PortfolioResult | None = None
    risk: RiskResult | None = None
