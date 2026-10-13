# filepath: src/trading/backtest/context.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.equity import EquityCurve, EquityPoint
from src.trading.core.events import PendingOrder, TargetEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.portfolio.state import PortfolioState
from src.trading.risk.evaluation import RiskObservation


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
    initial_equity: float | None = None
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


@dataclass(slots=True)
class BacktestResultContext:
    """Completed trading facts and derived results, outside the decision loop.

    Example:
        context = BacktestResultContext(state=completed_state, metrics=metrics)
    """

    state: BacktestState
    metrics: BacktestMetrics

    @property
    def prediction_refs(self) -> Sequence[Path]:
        """Expose only predictions actually used in this trading path.

        Example:
            observations = evaluate_prediction_records(context.prediction_refs, pm=pm)
        """
        return self.state.prediction_refs

    @property
    def decision_dates(self) -> Mapping[int, str]:
        """Expose the trading dates recorded with actual decisions.

        Example:
            trade_date = context.decision_dates[decision_ts_us]
        """
        return self.state.decision_dates

    @property
    def risk_observations(self) -> Sequence[RiskObservation]:
        """Expose rule observations without applying rules again.

        Example:
            result = evaluate_risk(context.risk_observations, configured_rules=rules)
        """
        return self.state.risk_observations
