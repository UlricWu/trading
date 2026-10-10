# filepath: src/trading/backtest/steps/backtest_layers.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.trading.backtest.context import BacktestContext, RiskEvaluationContext, SignalEvaluationContext
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.market.signals import SignalBatch, produce_signal
from src.trading.core.events import CancelOrders, OrderIntent, OrderTerms, Side, TargetEvent
from src.trading.core.time import TradingWindow, advance_trading_minutes
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.engine import make_executable_targets, mark_to_market
from src.trading.risk.base import RiskContext
from src.trading.risk.engine import RiskManager
from src.trading.risk.evaluation import RiskCase, RiskObservation, evaluate_risk_cases
from src.training.evaluation import evaluate_prediction_records
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class SignalEvaluationStep:
    """Evaluate the supplied fixed predictions with the shared model-target formula.

    Example:
        context = SignalEvaluationStep(pm).run(context)
    """

    pm: PathManager

    def run(self, context: SignalEvaluationContext) -> SignalEvaluationContext:
        """Retain observations in input order.

        Example:
            context = step.run(context)
        """
        context.observations = evaluate_prediction_records(context.prediction_refs, pm=self.pm)
        return context


@dataclass(frozen=True, slots=True)
class RiskEvaluationStep:
    """Evaluate frozen cases using the same manager as the full trading loop.

    Example:
        context = RiskEvaluationStep(risk).run(context)
    """

    risk: RiskManager

    def run(self, context: RiskEvaluationContext) -> RiskEvaluationContext:
        """Keep complete before/after and rule coverage facts.

        Example:
            context = step.run(context)
        """
        context.observations = evaluate_risk_cases(context.cases, risk=self.risk)
        return context


@dataclass(frozen=True, slots=True)
class AdvanceExecutionStep:
    """Change the account day and settle previously submitted orders before decisions.

    Example:
        context = AdvanceExecutionStep(execution, market).run(context)
    """

    execution: FakeExecution
    market: ReplayMarket

    def run(self, context: BacktestContext) -> BacktestContext:
        """Use a complete same-time observation batch before order expiry.

        Example:
            context = step.run(context)
        """
        self.market.on_time(context.timing.ts_us)
        context.state.portfolio_state.on_trading_day_start(context.timing.trade_date)
        self.execution.advance(ts_us=context.timing.ts_us,
                               observations=self.market.observations_at(context.timing.ts_us))
        return context


@dataclass(frozen=True, slots=True)
class AccountingStep:
    """Maintain event valuations and distinct official-day-end report samples.

    Example:
        context = AccountingStep(market).run(context)
    """

    market: ReplayMarket

    def run(self, context: BacktestContext) -> BacktestContext:
        """Mark existing positions without reading future daily closes.

        Example:
            context = step.run(context)
        """
        state = context.state
        prices = {symbol: float(price) for symbol, price in self.market.prices.items()}
        value, state.last_mark_prices = mark_to_market(positions=state.portfolio_state.positions,
            current_raw_prices=prices, last_mark_prices=state.last_mark_prices)
        state.equity_curve.append(ts_us=context.timing.ts_us,
            cash=float(state.portfolio_state.cash), market_value=value)
        session = self.market.session_at(context.timing.ts_us)
        if context.timing.ts_us == session.close_ts_us:
            close_symbols = self.market.daily_close_symbols.get(session.close_ts_us, set())
            if not state.portfolio_state.positions.keys() <= close_symbols:
                raise ValueError("end-of-day holdings require declared raw daily-close marks")
            state.daily_equity[session.trade_date] = state.equity_curve.points[-1]
        return context


@dataclass(frozen=True, slots=True)
class SignalStep:
    """Consume only the source batch belonging to the current decision.

    Example:
        context = SignalStep(batches, prediction_dir).run(context)
    """

    batches: Mapping[int, SignalBatch]
    prediction_dir: Path

    def run(self, context: BacktestContext) -> BacktestContext:
        """Save one inference result and never initialize or reset account state.

        Example:
            context = step.run(context)
        """
        batch = self.batches.get(context.timing.ts_us)
        if batch is not None:
            context.signal = produce_signal(batch, prediction_dir=self.prediction_dir)
            context.state.signal_tape.append(context.signal.event)
            context.state.prediction_refs.append(context.signal.prediction_ref)
            context.state.decision_dates[context.timing.ts_us] = context.signal.trade_date
        return context


@dataclass(frozen=True, slots=True)
class PortfolioStep:
    """Generate and record the complete original target before any risk adjustment.

    Example:
        context = PortfolioStep(constructor, market, target_capacity=None).run(context)
    """

    constructor: PortfolioConstructor
    market: ReplayMarket
    target_capacity: int | None

    def run(self, context: BacktestContext) -> BacktestContext:
        """Preserve missing-score positions and include outstanding-order symbols.

        Example:
            context = step.run(context)
        """
        signal = context.signal
        if signal is None:
            return context
        state = context.state
        raw = self.constructor.targets(ts_us=context.timing.ts_us,
            scores=signal.event.scores, state=state.portfolio_state)
        targets = make_executable_targets(raw_targets=raw, positions=state.portfolio_state.positions,
            current_raw_prices={s: float(p) for s, p in self.market.prices.items()},
            hold_symbols=set(signal.symbols) - signal.event.scores.keys(),
            pending_orders=tuple(state.pending_orders.values()), max_positions=self.target_capacity)
        event = TargetEvent(context.timing.ts_us, targets)
        state.target_tape.append(event)
        context.execution_target = event
        return context


@dataclass(frozen=True, slots=True)
class FixedHoldingStep:
    """Feed each clock tick to the strategy's own entry and exit plan.

    Example:
        context = FixedHoldingStep(constructor, market).run(context)
    """

    constructor: FixedHoldingConstructor
    market: ReplayMarket

    def run(self, context: BacktestContext) -> BacktestContext:
        """Preserve scheduled targets even without a new model signal.

        Example:
            context = step.run(context)
        """
        event = self.constructor.targets(ts_us=context.timing.ts_us,
            trade_date=context.timing.trade_date,
            scores=context.signal.event.scores if context.signal is not None else None,
            state=context.state.portfolio_state, prices=self.market.prices,
            pending_orders=tuple(context.state.pending_orders.values()))
        if event is not None:
            context.state.target_tape.append(event)
            context.execution_target = event
        context.requested_wakeup_ts_us.update(self.constructor.wakeups())
        return context


@dataclass(frozen=True, slots=True)
class RiskDecisionStep:
    """Check new requests or current commitments without consulting historical targets.

    Example:
        context = RiskDecisionStep(risk, market, quantity_steps).run(context)
    """

    risk: RiskManager
    market: ReplayMarket
    quantity_steps: Mapping[str, int]

    def run(self, context: BacktestContext) -> BacktestContext:
        """Record an immutable case before submitting its explicit risk actions.

        Example:
            context = step.run(context)
        """
        state = context.state
        account = state.portfolio_state
        equity = state.equity_curve.points[-1]
        original = context.execution_target
        case = RiskCase(str(context.timing.ts_us), context.timing.trade_date,
            original.targets if original is not None else None,
            RiskContext(context.timing.ts_us, {s: float(p) for s, p in self.market.prices.items()},
                equity.equity, float(account.cash), equity.peak_equity, account.positions,
                tuple(state.pending_orders.values()), self.quantity_steps))
        decision = self.risk.apply(target=case.target, ctx=case.context)
        state.risk_observations.append(RiskObservation(case, decision))
        context.execution_target = (TargetEvent(context.timing.ts_us, decision.adjusted)
                                    if decision.adjusted is not None else None)
        context.cancel_order_ids = decision.cancel_order_ids
        return context


@dataclass(frozen=True, slots=True)
class SubmitTargetsStep:
    """Submit actions through H03 and request actual pending-order deadlines.

    Example:
        context = SubmitTargetsStep(execution, market, windows, 2).run(context)
    """

    execution: FakeExecution
    market: ReplayMarket
    windows: Sequence[TradingWindow]
    order_window_minutes: int

    def run(self, context: BacktestContext) -> BacktestContext:
        """Reconcile before cancellation so reduced orders retain original terms.

        Example:
            context = step.run(context)
        """
        target = context.execution_target
        if target is not None:
            new_symbols = []
            for symbol, quantity in target.targets.items():
                delta = quantity - context.state.portfolio_state.positions.get(symbol, 0)
                side = Side.BUY if delta > 0 else Side.SELL
                covered = sum(order.remaining_qty for order in context.state.pending_orders.values()
                              if order.intent.symbol == symbol and order.intent.side == side)
                if abs(delta) > covered:
                    new_symbols.append(symbol)
            terms = {}
            if new_symbols:
                expiry = advance_trading_minutes(start_ts_us=context.timing.ts_us,
                    minutes=self.order_window_minutes, windows=self.windows)
                terms = {symbol: OrderTerms(context.timing.ts_us, expiry) for symbol in new_symbols}
            self.execution.submit_targets(target=target, cancel_order_ids=context.cancel_order_ids,
                                          terms=terms, market=self.market)
        elif context.cancel_order_ids:
            self.execution.cancel_orders(ts_us=context.timing.ts_us, order_ids=context.cancel_order_ids)
        context.requested_wakeup_ts_us.update(order.intent.expires_ts_us
                                              for order in context.state.pending_orders.values())
        context.execution_target = None
        return context


@dataclass(frozen=True, slots=True)
class RecordedOrdersStep:
    """Replay frozen commands in their original order without invoking a strategy.

    Example:
        context = RecordedOrdersStep(execution, market, commands).run(context)
    """

    execution: FakeExecution
    market: ReplayMarket
    commands: Mapping[int, tuple[OrderIntent | CancelOrders, ...]]

    def run(self, context: BacktestContext) -> BacktestContext:
        """Use the same order acceptance and cancellation boundary as target submission.

        Example:
            context = step.run(context)
        """
        for command in self.commands.get(context.timing.ts_us, ()):
            if isinstance(command, OrderIntent):
                self.execution.submit_orders(orders=(command,), market=self.market)
            else:
                self.execution.cancel_orders(ts_us=command.ts_us, order_ids=command.order_ids)
        context.requested_wakeup_ts_us.update(order.intent.expires_ts_us
                                              for order in context.state.pending_orders.values())
        return context
