# filepath: src/trading/backtest/context.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.equity import EquityCurve, EquityPoint
from src.trading.core.events import PendingOrder, TargetEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.portfolio.state import PortfolioState
from src.trading.risk.evaluation import RiskCase, RiskObservation
from src.training.evaluation import ICObservation


@dataclass(slots=True)
class SignalEvaluationContext:
    """Single-pass evaluation facts with no trading state.

    Example:
        context = SignalEvaluationContext((prediction_ref,), {decision_us: "2026-07-03"})
    """

    prediction_refs: tuple[Path, ...]
    decision_dates: dict[int, str]
    observations: tuple[ICObservation, ...] = ()


@dataclass(slots=True)
class RiskEvaluationContext:
    """Fixed risk inputs and actual rule observations, never a mutable account.

    Example:
        context = RiskEvaluationContext(cases=(case,))
    """

    cases: tuple[RiskCase, ...]
    observations: tuple[RiskObservation, ...] = ()


@dataclass(slots=True)
class BacktestState:
    """Run-owned trading facts; no future labels or accumulated evaluation frames.

    Example:
        state = BacktestState(portfolio_state=account)
    """

    portfolio_state: PortfolioState
    execution_ledger: ExecutionLedger = field(default_factory=ExecutionLedger)
    pending_orders: dict[int, PendingOrder] = field(default_factory=dict)
    equity_curve: EquityCurve = field(default_factory=EquityCurve)
    daily_equity: dict[str, EquityPoint] = field(default_factory=dict)
    signal_tape: SignalTape = field(default_factory=SignalTape)
    target_tape: TargetTape = field(default_factory=TargetTape)
    risk_observations: list[RiskObservation] = field(default_factory=list)
    prediction_refs: list[Path] = field(default_factory=list)
    decision_dates: dict[int, str] = field(default_factory=dict)
    last_mark_prices: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BacktestContext:
    """Current visible facts and explicit actions for a single clock tick.

    Example:
        context = BacktestContext(timing=timing, state=state)
    """

    timing: BacktestTiming
    state: BacktestState
    signal: RecordedSignalBatch | None = None
    execution_target: TargetEvent | None = None
    cancel_order_ids: tuple[int, ...] = ()
    requested_wakeup_ts_us: set[int] = field(default_factory=set)
