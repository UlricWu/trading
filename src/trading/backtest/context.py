# filepath: src/trading/backtest/context.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market.signals import SignalBatch
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.core.equity import EquityCurve, EquityPoint
from src.trading.core.events import PendingOrder
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


@dataclass(frozen=True, slots=True)
class BacktestInputs:
    """Validated market facts and signal batches or frozen commands, without labels.

    Example:
        inputs = BacktestInputs(market=market_source, decisions=signal_inputs_by_ts_us)
    """

    market: ReplayMarketInput
    decisions: Mapping[int, SignalBatch] | OrderCommandSchedule

    def __post_init__(self) -> None:
        if not isinstance(self.decisions, OrderCommandSchedule):
            object.__setattr__(self, "decisions", MappingProxyType(dict(self.decisions)))


@dataclass(slots=True)
class BacktestContext:
    """Carry one run's prepared inputs, completed trading facts and derived results.

    None means the producing Step has not completed. Ordered consumers use the
    corresponding producer's output; tick actions and runtime dependencies stay local.

    Example:
        context = BacktestContext(metrics=metrics)
    """

    metrics: BacktestMetrics
    inputs: BacktestInputs | None = None
    state: BacktestState | None = None
