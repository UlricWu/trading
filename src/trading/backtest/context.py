# filepath: src/trading/backtest/context.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import MarkObservation, ReplayMarket, ReplayMarketInput, ReplaySession
from src.trading.backtest.market.signals import RecordedSignalBatch, SignalBatch
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.core.equity import EquityCurve, EquityPoint
from src.trading.core.events import PendingOrder, TargetEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.portfolio.state import PortfolioState
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.risk import RiskManager
from src.trading.risk.evaluation import RiskObservation


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """Record one held position's initial or closing valuation basis.

    Example:
        mark = PositionValuation(close_us, "2026-07-20", "600000", 100,
            Decimal("10"), prior_close_us, "2026-07-17", "daily_close", "suspension")
    """

    ts_us: int
    trade_date: str
    symbol: str
    qty: int
    price: Decimal
    price_ts_us: int
    price_trade_date: str
    price_kind: Literal["mark", "daily_close", "execution"]
    reason: Literal["initial", "daily_close", "suspension", "missing_daily_close"]


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
    valuation_marks: dict[tuple[int, str], tuple[PositionValuation, ...]] = field(default_factory=dict)


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
class TickState:
    """Carry only the current tick's decisions between independently assembled Steps.

    None targets mean no submission; explicit zero quantities mean liquidation.
    A new instance on every tick prevents old targets or cancellations from recurring.

    Example:
        tick = TickState(ts_us=decision_us, session=market_input.session_at(decision_us))
    """

    ts_us: int
    session: ReplaySession
    signal: RecordedSignalBatch | None = None
    original_target: TargetEvent | None = None
    execution_target: TargetEvent | None = None
    cancel_order_ids: tuple[int, ...] = ()
    risk_observation: RiskObservation | None = None


@dataclass(slots=True)
class BacktestContext:
    """Own prepared inputs, account runtime, current decisions and accumulated facts.

    Sessions and market_input carry startup results into PrepareInputsStep, which
    combines the market with decision inputs. Runtime is absent until initialization. State accumulates
    during replay; result Steps consume it only after the clock finishes. Tick is
    present only during replay. Configuration and external services stay bound to Steps.

    Example:
        context = BacktestContext(metrics=metrics)
    """

    metrics: BacktestMetrics
    sessions: tuple[ReplaySession, ...] = ()
    market_input: ReplayMarketInput | None = None
    inputs: BacktestInputs | None = None
    initial_marks: tuple[MarkObservation, ...] = ()
    state: BacktestState | None = None
    market: ReplayMarket | None = None
    executor: AccountExecutionSimulator | None = None
    constructor: PortfolioConstructor | FixedHoldingConstructor | None = None
    risk: RiskManager | None = None
    clock: ReplayClock | None = None
    tick: TickState | None = None
