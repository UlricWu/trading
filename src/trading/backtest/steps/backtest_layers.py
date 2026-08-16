# filepath: src/trading/backtest/steps/backtest_layers.py
"""Explicit daily-alpha backtest layer operations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from src import logs
from src.access import Access
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.events import SignalEvent, TargetEvent
from src.trading.engines.backtest_eval import (
    ExecutionQualityFrame,
    FullBacktestFrame,
    RiskEffectFrame,
    SignalEvalFrame,
    TradableAlphaFrame,
    execution_quality_frame,
    full_backtest_frame,
    risk_effect_frame,
    signal_eval_frame,
    tradable_alpha_frame,
)
from src.trading.engines.replay import make_executable_targets, mark_to_market
from src.trading.execution.engine import ExecutionOrchestrator
from src.trading.execution.ideal import IdealExecution
from src.trading.market.daily_signal_data import (
    RAW_PRICE_COL,
    read_daily_raw_signal_view_data,
    read_raw_close,
)
from src.trading.market.daily_view import DailyView
from src.trading.market.data_view import MarketDataView
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.risk.base import RiskContext
from src.trading.risk.engine import NoOpRiskManager, RiskManager
from src.trading.signal.base import SignalProvider
from src.trading.sim.kernel import BacktestKernel, BarContext
from src.trading.sim.session import ReplaySession
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class SignalResult:
    """Carry one timing's bar, prices, and scores.

    Example:
        trade_date = signal_result.bar.trade_date
    """

    bar: BarContext
    bars_count: int
    raw_prices: dict[str, float]
    scores: dict[str, float]


@dataclass(frozen=True, slots=True)
class SignalEvaluationResult:
    """Carry forward prices with the signal evaluation frame.

    Example:
        frame = signal_evaluation.frame
    """

    forward_raw_prices: dict[str, float]
    frame: SignalEvalFrame


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    """Carry executable portfolio targets before risk adjustment.

    Example:
        executable_targets = portfolio_result.targets
    """

    targets: dict[str, int]


@dataclass(frozen=True, slots=True)
class RiskResult:
    """Carry adjusted targets with their risk evaluation frame.

    Example:
        adjusted_targets = risk_result.targets
    """

    targets: dict[str, int]
    frame: RiskEffectFrame


@dataclass(frozen=True, slots=True)
class AccountingResult:
    """Carry one mark-to-market result for workflow-owned persistence.

    Example:
        state.last_mark_prices = accounting.last_mark_prices
    """

    market_value: float
    last_mark_prices: dict[str, float]


class SignalStep:
    """Produce score facts for one explicit daily-alpha timing.

    Example:
        operation = SignalStep(
            access=access,
            pm=path_manager,
            min_listing_calendar_days=120,
            signal=signal,
            feature_set="daily",
            feature_version="v1",
            feature_names=("momentum",),
        )
        result = operation(timing, state)
    """

    def __init__(
        self,
        *,
        access: Access,
        pm: PathManager,
        min_listing_calendar_days: int,
        signal: SignalProvider,
        feature_set: str,
        feature_version: str,
        feature_names: Sequence[str],
    ) -> None:
        """Bind market access and signal capabilities.

        Example:
            operation = SignalStep(
                access=access,
                pm=path_manager,
                min_listing_calendar_days=120,
                signal=signal,
                feature_set="daily",
                feature_version="v1",
                feature_names=("momentum",),
            )
        """
        self._access = access
        self._pm = pm
        self._min_listing_calendar_days = min_listing_calendar_days
        self._signal = signal
        self._feature_set = feature_set
        self._feature_version = feature_version
        self._feature_names = tuple(feature_names)

    def __call__(
        self,
        timing: BacktestTiming,
        state: BacktestState,
    ) -> SignalResult:
        """Return the single executable bar, raw prices, and scores.

        Example:
            result = operation(timing, state)
        """
        trade_date = timing.signal_date
        prices = read_raw_close(
            access=self._access,
            trade_date=trade_date,
            symbols=None,
        )
        raw_prices = {
            str(row.symbol): float(cast(float, row.close))
            for row in prices.itertuples(index=False)
        }
        signal_symbols = self._access.universe(
            trade_date=trade_date,
            min_listing_calendar_days=self._min_listing_calendar_days,
        )
        view_data = read_daily_raw_signal_view_data(
            access=self._access,
            pm=self._pm,
            symbols=signal_symbols,
            price_date=trade_date,
            feature_date=timing.signal_date,
            feature_set=self._feature_set,
            feature_version=self._feature_version,
            feature_names=self._feature_names,
        )
        data_view = DailyView(
            view_data,
            trade_date=trade_date,
            price_column=RAW_PRICE_COL,
        )
        session = ReplaySession.from_data_view(data_view)
        bars = BacktestKernel(
            clock=session.clock,
            data_view=session.data_view,
        ).run()
        trade_bars = [bar for bar in bars if bar.should_trade]
        if len(trade_bars) != 1:
            raise RuntimeError(
                "[SignalStep] expected one trading bar for "
                f"trade_date={trade_date}; got={len(trade_bars)}"
            )

        state.portfolio_state.on_trading_day_start(trade_date)
        bar = trade_bars[0]
        scores = self._signal.scores(
            ts_us=bar.ts_us,
            data_view=bar.data_view,
            symbols=signal_symbols,
        )
        if not scores:
            raise RuntimeError(f"[SignalStep] no scores generated for {trade_date}")
        return SignalResult(
            bar=bar,
            bars_count=len(bars),
            raw_prices=raw_prices,
            scores=dict(scores),
        )

    def run(self, context: BacktestContext) -> BacktestContext:
        """Produce and record the signal facts for the Context timing.

        Example:
            signaled_context = operation.run(context)
        """
        signal = self(context.timing, context.state)
        context.signal = signal
        context.state.signal_tape.append(
            SignalEvent(
                ts_us=signal.bar.ts_us,
                scores=signal.scores,
                meta={"trade_date": signal.bar.trade_date},
            )
        )
        context.state.bar_count += signal.bars_count
        context.state.signal_count += len(signal.scores)
        return context


class SignalEvalStep:
    """Evaluate one signal result against the T+1 raw close.

    Example:
        operation = SignalEvalStep(access=access)
        evaluation = operation(timing, signal_result)
    """

    def __init__(self, *, access: Access) -> None:
        """Bind the processed market-data capability.

        Example:
            operation = SignalEvalStep(access=access)
        """
        self._access = access

    def __call__(
        self,
        timing: BacktestTiming,
        signal: SignalResult,
    ) -> SignalEvaluationResult:
        """Return forward prices and one signal evaluation frame.

        Example:
            evaluation = operation(timing, signal_result)
        """
        forward_prices = read_raw_close(
            access=self._access,
            trade_date=timing.forward_date,
            symbols=None,
        )
        forward_raw_prices = {
            str(row.symbol): float(cast(float, row.close))
            for row in forward_prices.itertuples(index=False)
        }
        frame = signal_eval_frame(
            scores=signal.scores,
            entry_prices=signal.raw_prices,
            exit_prices=forward_raw_prices,
        )
        frame.update(
            {
                "trade_date": signal.bar.trade_date,
                "forward_date": timing.forward_date,
                "ts_us": int(signal.bar.ts_us),
            }
        )
        return SignalEvaluationResult(
            forward_raw_prices=forward_raw_prices,
            frame=frame,
        )

    def run(self, context: BacktestContext) -> BacktestContext:
        """Evaluate and record the Context signal against its forward date.

        Example:
            evaluated_context = operation.run(signaled_context)
        """
        signal = context.signal
        if signal is None:
            raise RuntimeError("SignalEvalStep requires a signal result")
        evaluation = self(context.timing, signal)
        context.signal_evaluation = evaluation
        context.state.signal_eval_frames.append(evaluation.frame)
        return context


class TradableAlphaEvalStep:
    """Build one T+1 tradable-alpha evaluation frame.

    Example:
        frame = TradableAlphaEvalStep()(timing, signal_result, evaluation)
    """

    def __call__(
        self,
        timing: BacktestTiming,
        signal: SignalResult,
        evaluation: SignalEvaluationResult,
    ) -> TradableAlphaFrame:
        """Return one tradable-alpha frame without mutating state.

        Example:
            frame = operation(timing, signal_result, evaluation)
        """
        frame = tradable_alpha_frame(
            scores=signal.scores,
            entry_prices=signal.raw_prices,
            exit_prices=evaluation.forward_raw_prices,
        )
        frame.update(
            {
                "trade_date": signal.bar.trade_date,
                "forward_date": timing.forward_date,
                "ts_us": int(signal.bar.ts_us),
            }
        )
        return frame

    def run(self, context: BacktestContext) -> BacktestContext:
        """Build and record tradable-alpha facts for the Context timing.

        Example:
            evaluated_context = operation.run(signal_evaluated_context)
        """
        signal = context.signal
        evaluation = context.signal_evaluation
        if signal is None or evaluation is None:
            raise RuntimeError(
                "TradableAlphaEvalStep requires signal evaluation results"
            )
        context.state.tradable_alpha_frames.append(
            self(context.timing, signal, evaluation)
        )
        return context


class PortfolioStep:
    """Transform score facts into executable target facts.

    Example:
        operation = PortfolioStep(constructor=constructor, target_capacity=None)
        portfolio = operation(signal_result, state)
    """

    def __init__(
        self,
        *,
        constructor: PortfolioConstructor,
        target_capacity: int | None,
    ) -> None:
        """Bind the portfolio constructor and optional target capacity.

        Example:
            operation = PortfolioStep(
                constructor=constructor,
                target_capacity=None,
            )
        """
        self._constructor = constructor
        self._target_capacity = target_capacity

    def __call__(
        self,
        signal: SignalResult,
        state: BacktestState,
    ) -> PortfolioResult:
        """Return raw and executable targets for one timing.

        Example:
            portfolio = operation(signal_result, state)
        """
        targets = self._constructor.targets(
            ts_us=signal.bar.ts_us,
            scores=signal.scores,
            state=state.portfolio_state,
        )
        executable = make_executable_targets(
            raw_targets=targets,
            positions=state.portfolio_state.positions,
            current_raw_prices=signal.raw_prices,
            max_positions=self._target_capacity,
        )
        return PortfolioResult(targets=dict(executable))

    def run(self, context: BacktestContext) -> BacktestContext:
        """Build and attach executable portfolio targets for the Context.

        Example:
            portfolio_context = operation.run(evaluated_context)
        """
        signal = context.signal
        if signal is None:
            raise RuntimeError("PortfolioStep requires a signal result")
        context.portfolio = self(signal, context.state)
        return context


class RiskEvalStep:
    """Apply risk and return adjusted targets with side facts.

    Example:
        operation = RiskEvalStep(risk=risk_manager)
        risk_result = operation(signal_result, portfolio, state)
    """

    def __init__(self, *, risk: RiskManager | NoOpRiskManager) -> None:
        """Bind one risk implementation.

        Example:
            operation = RiskEvalStep(risk=risk_manager)
        """
        self._risk = risk

    def __call__(
        self,
        signal: SignalResult,
        portfolio: PortfolioResult,
        state: BacktestState,
    ) -> RiskResult:
        """Return adjusted targets and one risk evaluation frame.

        Example:
            risk_result = operation(signal_result, portfolio, state)
        """
        latest = state.equity_curve.latest
        equity = (
            float(latest.equity)
            if latest is not None
            else float(state.portfolio_state.cash)
        )
        peak_equity = (
            float(state.equity_curve.peak_equity) if latest is not None else equity
        )
        decision = self._risk.apply(
            target=portfolio.targets,
            ctx=RiskContext(
                ts_us=signal.bar.ts_us,
                prices=dict(signal.raw_prices),
                equity=equity,
                cash=float(state.portfolio_state.cash),
                peak_equity=peak_equity,
                positions=dict(state.portfolio_state.positions),
                meta={"trade_date": signal.bar.trade_date},
            ),
        )
        adjusted_targets = dict(decision.adjusted)
        frame: RiskEffectFrame = {
            **risk_effect_frame(
                targets_before=portfolio.targets,
                targets_after=adjusted_targets,
                prices=signal.raw_prices,
                equity=equity,
                blocked=decision.blocked,
                scaled=decision.scaled,
                reason=decision.reason,
            ),
            "trade_date": signal.bar.trade_date,
            "ts_us": int(signal.bar.ts_us),
        }
        return RiskResult(targets=adjusted_targets, frame=frame)

    def run(self, context: BacktestContext) -> BacktestContext:
        """Apply risk and record adjusted targets for the Context timing.

        Example:
            risk_context = operation.run(portfolio_context)
        """
        signal = context.signal
        portfolio = context.portfolio
        if signal is None or portfolio is None:
            raise RuntimeError("RiskEvalStep requires signal and portfolio results")
        risk = self(signal, portfolio, context.state)
        context.risk = risk
        context.state.target_tape.append(
            TargetEvent(
                ts_us=signal.bar.ts_us,
                targets=risk.targets,
                meta={"trade_date": signal.bar.trade_date},
            )
        )
        context.state.risk_decision_frames.append(risk.frame)
        return context


class ExecutionEvalStep:
    """Execute adjusted targets and return execution quality facts.

    Example:
        operation = ExecutionEvalStep(execution=execution)
        frame = operation(signal_result, risk_result, state)
    """

    def __init__(
        self,
        *,
        execution: IdealExecution | ExecutionOrchestrator,
    ) -> None:
        """Bind one execution implementation.

        Example:
            operation = ExecutionEvalStep(execution=execution)
        """
        self._execution = execution

    def __call__(
        self,
        signal: SignalResult,
        risk: RiskResult,
        state: BacktestState,
    ) -> ExecutionQualityFrame:
        """Execute targets and return one evaluation frame.

        Example:
            frame = operation(signal_result, risk_result, state)
        """
        execution_view = _RawCloseExecutionView(
            base=signal.bar.data_view,
            prices=signal.raw_prices,
        )
        execution_view.on_time(signal.bar.ts_us)
        positions_before = dict(state.portfolio_state.positions)
        records_before = len(state.execution_ledger.records)
        fills = self._execution.execute_targets(
            ts_us=signal.bar.ts_us,
            targets=risk.targets,
            data_view=execution_view,
            state=state.portfolio_state,
            ledger=state.execution_ledger,
        )
        frame: ExecutionQualityFrame = {
            **execution_quality_frame(
                targets=risk.targets,
                positions_before=positions_before,
                prices=signal.raw_prices,
                fills=fills,
                ledger_records=state.execution_ledger.records[records_before:],
            ),
            "trade_date": signal.bar.trade_date,
            "ts_us": int(signal.bar.ts_us),
            "ledger_records_added": int(
                len(state.execution_ledger.records) - records_before
            ),
        }
        return frame

    def run(self, context: BacktestContext) -> BacktestContext:
        """Execute targets and record execution quality for the Context.

        Example:
            executed_context = operation.run(risk_context)
        """
        signal = context.signal
        risk = context.risk
        if signal is None or risk is None:
            raise RuntimeError("ExecutionEvalStep requires signal and risk results")
        context.state.execution_eval_frames.append(self(signal, risk, context.state))
        return context


class AccountingStep:
    """Calculate one account valuation update after execution.

    Example:
        accounting = AccountingStep()(signal_result, state)
    """

    def __call__(
        self,
        signal: SignalResult,
        state: BacktestState,
    ) -> AccountingResult:
        """Return current market value and the next mark-price state.

        Example:
            accounting = operation(signal_result, state)
        """
        market_value, last_mark_prices = mark_to_market(
            positions=state.portfolio_state.positions,
            current_raw_prices=signal.raw_prices,
            last_mark_prices=state.last_mark_prices,
        )
        return AccountingResult(
            market_value=float(market_value),
            last_mark_prices=last_mark_prices,
        )

    def run(self, context: BacktestContext) -> BacktestContext:
        """Apply one valuation update to the persistent Context state.

        Example:
            accounted_context = AccountingStep().run(executed_context)
        """
        signal = context.signal
        if signal is None:
            raise RuntimeError("AccountingStep requires a signal result")
        accounting = self(signal, context.state)
        context.state.last_mark_prices = dict(accounting.last_mark_prices)
        context.state.equity_curve.append(
            ts_us=signal.bar.ts_us,
            cash=float(context.state.portfolio_state.cash),
            market_value=accounting.market_value,
        )
        context.state.trade_dates.append(signal.bar.trade_date)
        return context


class FullBacktestStep:
    """Build one full-backtest frame after accounting.

    Example:
        frame = FullBacktestStep()(signal_result, state)
    """

    def __call__(
        self,
        signal: SignalResult,
        state: BacktestState,
    ) -> FullBacktestFrame:
        """Return one full-backtest frame and emit its operational log.

        Example:
            frame = operation(signal_result, state)
        """
        frame: FullBacktestFrame = {
            **full_backtest_frame(
                equity_points=state.equity_curve.points,
                positions=state.portfolio_state.positions,
            ),
            "trade_date": signal.bar.trade_date,
            "ts_us": int(signal.bar.ts_us),
        }
        logs.info(
            f"processed trade_date={signal.bar.trade_date} "
            f"symbols={len(signal.bar.symbols)} bars={signal.bars_count} "
            f"positions={len(state.portfolio_state.positions)} "
            f"ledger_records={len(state.execution_ledger.records)}"
        )
        return frame

    def run(self, context: BacktestContext) -> BacktestContext:
        """Build and record the full-backtest frame for the Context timing.

        Example:
            completed_context = FullBacktestStep().run(accounted_context)
        """
        signal = context.signal
        if signal is None:
            raise RuntimeError("FullBacktestStep requires a signal result")
        context.state.full_backtest_frames.append(self(signal, context.state))
        return context


class _RawCloseExecutionView(MarketDataView):
    """Execution price view backed by current-day raw close availability."""

    def __init__(
        self,
        *,
        base: MarketDataView,
        prices: Mapping[str, float],
    ) -> None:
        self._base = base
        self._prices = {str(symbol): float(price) for symbol, price in prices.items()}
        self._current_ts: int | None = None

    def on_time(self, ts_us: int) -> None:
        self._base.on_time(ts_us)
        self._current_ts = int(ts_us)

    def time_bounds_us(self) -> tuple[int, int]:
        return self._base.time_bounds_us()

    def bar_timestamps_us(self) -> list[int]:
        return self._base.bar_timestamps_us()

    def get_price(self, symbol: str) -> float | None:
        self._require_active()
        value = self._prices.get(str(symbol))
        if value is None or not math.isfinite(float(value)):
            return None
        return float(value)

    def get_feature_matrix(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        return self._base.get_feature_matrix(symbols)

    def get_price_vector(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        return np.array(
            [
                np.nan if (price := self.get_price(str(symbol))) is None else price
                for symbol in symbols
            ],
            dtype=np.float64,
        )

    @property
    def frequency(self) -> str:
        return self._base.frequency

    @property
    def symbols(self) -> list[str]:
        return self._base.symbols

    @property
    def trade_date(self) -> str:
        return self._base.trade_date

    def _require_active(self) -> None:
        if self._current_ts is None:
            raise RuntimeError(
                "_RawCloseExecutionView.on_time(ts_us) must be called before query"
            )
