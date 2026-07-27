# filepath: src/trading/pipeline/steps/backtest_layers.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

import numpy as np

from src.access import Access
from src.pipeline.phase import TRADING
from src.pipeline.step import PipelineStep
from src.trading.core.events import SignalEvent, TargetEvent
from src.trading.engines.backtest_eval import (
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
from src.trading.pipeline.context import TradingContext
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.risk.base import RiskContext
from src.trading.risk.engine import NoOpRiskManager, RiskManager
from src.trading.signal.base import SignalProvider
from src.trading.sim.kernel import BacktestKernel
from src.trading.sim.session import ReplaySession
from src import logs


class SignalStep(PipelineStep[TradingContext]):
    """Produce score facts for the current daily_alpha timing."""

    stage = "signal"

    def __init__(
        self,
        *,
        signal: SignalProvider,
        feature_set: str,
        feature_version: str,
        feature_names: Sequence[str],
    ) -> None:
        super().__init__()
        self.signal = signal
        self.feature_set = feature_set
        self.feature_version = feature_version
        self.feature_names = list(feature_names)

    def run(self, ctx: TradingContext) -> TradingContext:
        timing = ctx.backtest_timing
        trade_date = timing.signal_date
        prices = read_raw_close(
            pm=ctx.pm,
            trade_date=trade_date,
            symbols=None,
        )
        current_raw_prices = {
            str(row.symbol): float(row.close) for row in prices.itertuples(index=False)
        }
        signal_symbols = Access(
            pm=ctx.pm,
            processed_version="v1",
        ).universe(
            trade_date=trade_date,
            min_listing_calendar_days=ctx.cfg.min_listing_calendar_days,
        )
        view_data = read_daily_raw_signal_view_data(
            pm=ctx.pm,
            symbols=signal_symbols,
            price_date=trade_date,
            feature_date=timing.feature_date,
            feature_set=self.feature_set,
            feature_version=self.feature_version,
            feature_names=self.feature_names,
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

        ctx.portfolio_state.on_trading_day_start(trade_date)

        bar = trade_bars[0]
        scores = self.signal.scores(
            ts_us=bar.ts_us,
            data_view=bar.data_view,
            symbols=signal_symbols,
        )
        if not scores:
            raise RuntimeError(f"[SignalStep] no scores generated for {trade_date}")

        ctx.current_bar = bar
        ctx.current_bars_count = len(bars)
        ctx.current_raw_prices = current_raw_prices
        ctx.current_signal_symbols = signal_symbols
        ctx.current_scores = scores
        ctx.signal_tape.append(
            SignalEvent(
                ts_us=bar.ts_us,
                scores=scores,
                meta={"trade_date": trade_date},
            )
        )
        ctx.bar_count += len(bars)
        ctx.signal_count += len(scores)
        return ctx


class SignalEvalStep(PipelineStep[TradingContext]):
    """Evaluate signal scores against the T+1 raw-close label."""

    stage = "signal_eval"

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        forward_prices = read_raw_close(
            pm=ctx.pm,
            trade_date=ctx.backtest_timing.forward_date,
            symbols=None,
        )
        ctx.current_forward_raw_prices = {
            str(row.symbol): float(row.close)
            for row in forward_prices.itertuples(index=False)
        }
        frame = signal_eval_frame(
            scores=ctx.current_scores,
            entry_prices=ctx.current_raw_prices,
            exit_prices=ctx.current_forward_raw_prices,
        )
        frame.update(
            {
                "trade_date": bar.trade_date,
                "forward_date": ctx.backtest_timing.forward_date,
                "ts_us": int(bar.ts_us),
            }
        )
        ctx.signal_eval_frames.append(frame)
        return ctx


class TradableAlphaEvalStep(PipelineStep[TradingContext]):
    """Build T+1 tradable-alpha label facts without creating orders."""

    stage = "tradable_alpha_eval"

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        frame = tradable_alpha_frame(
            scores=ctx.current_scores,
            entry_prices=ctx.current_raw_prices,
            exit_prices=ctx.current_forward_raw_prices,
        )
        frame.update(
            {
                "trade_date": bar.trade_date,
                "forward_date": ctx.backtest_timing.forward_date,
                "ts_us": int(bar.ts_us),
            }
        )
        ctx.tradable_alpha_frames.append(frame)
        return ctx


class PortfolioStep(PipelineStep[TradingContext]):
    """Transform score facts into target position facts."""

    stage = "portfolio"

    def __init__(
        self,
        *,
        constructor: PortfolioConstructor,
        target_capacity: int | None,
    ) -> None:
        super().__init__()
        self.constructor = constructor
        self.target_capacity = target_capacity

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        targets = self.constructor.targets(
            ts_us=bar.ts_us,
            scores=ctx.current_scores,
            state=ctx.portfolio_state,
        )
        int_targets = make_executable_targets(
            raw_targets=targets,
            positions=ctx.portfolio_state.positions,
            current_raw_prices=ctx.current_raw_prices,
            max_positions=self.target_capacity,
        )
        ctx.current_raw_targets = dict(int_targets)
        ctx.current_targets = int_targets
        return ctx


class RiskEvalStep(PipelineStep[TradingContext]):
    """Apply risk to targets and keep decisions as side facts."""

    stage = "risk_eval"

    def __init__(self, *, risk: RiskManager | NoOpRiskManager) -> None:
        super().__init__()
        self.risk = risk

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        latest = ctx.equity_curve.latest
        equity = (
            float(latest.equity)
            if latest is not None
            else float(ctx.portfolio_state.cash)
        )
        peak_equity = (
            float(ctx.equity_curve.peak_equity) if latest is not None else equity
        )
        decision = self.risk.apply(
            target=ctx.current_targets,
            ctx=RiskContext(
                ts_us=bar.ts_us,
                prices=dict(ctx.current_raw_prices),
                equity=equity,
                cash=float(ctx.portfolio_state.cash),
                peak_equity=peak_equity,
                positions=dict(ctx.portfolio_state.positions),
                meta={"trade_date": bar.trade_date},
            ),
        )
        ctx.current_targets = dict(decision.adjusted)
        ctx.target_tape.append(
            TargetEvent(
                ts_us=bar.ts_us,
                targets=ctx.current_targets,
                meta={"trade_date": bar.trade_date},
            )
        )
        ctx.risk_decision_frames.append(
            {
                **risk_effect_frame(
                    targets_before=ctx.current_raw_targets,
                    targets_after=ctx.current_targets,
                    prices=ctx.current_raw_prices,
                    equity=equity,
                    blocked=decision.blocked,
                    scaled=decision.scaled,
                    reason=decision.reason,
                ),
                "trade_date": bar.trade_date,
                "ts_us": int(bar.ts_us),
            }
        )
        return ctx


class ExecutionEvalStep(PipelineStep[TradingContext]):
    """Execute target facts; execution does not consume risk decisions."""

    stage = "execution_eval"

    def __init__(
        self,
        *,
        execution: IdealExecution | ExecutionOrchestrator,
    ) -> None:
        super().__init__()
        self.execution = execution

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        execution_view = _RawCloseExecutionView(
            base=bar.data_view,
            prices=ctx.current_raw_prices,
        )
        execution_view.on_time(bar.ts_us)
        positions_before = dict(ctx.portfolio_state.positions)
        before_records = len(ctx.execution_ledger.records)
        fills = self.execution.execute_targets(
            ts_us=bar.ts_us,
            targets=ctx.current_targets,
            data_view=execution_view,
            state=ctx.portfolio_state,
            ledger=ctx.execution_ledger,
        )
        ctx.current_fills = fills
        new_records = ctx.execution_ledger.records[before_records:]
        ctx.execution_eval_frames.append(
            {
                **execution_quality_frame(
                    targets=ctx.current_targets,
                    positions_before=positions_before,
                    prices=ctx.current_raw_prices,
                    fills=fills,
                    ledger_records=new_records,
                ),
                "trade_date": bar.trade_date,
                "ts_us": int(bar.ts_us),
                "ledger_records_added": int(
                    len(ctx.execution_ledger.records) - before_records
                ),
            }
        )
        return ctx


class AccountingStep(PipelineStep[TradingContext]):
    """Update account valuation facts after execution."""

    stage = "accounting"

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        market_value, ctx.last_mark_prices = mark_to_market(
            positions=ctx.portfolio_state.positions,
            current_raw_prices=ctx.current_raw_prices,
            last_mark_prices=ctx.last_mark_prices,
        )
        ctx.equity_curve.append(
            ts_us=bar.ts_us,
            cash=float(ctx.portfolio_state.cash),
            market_value=float(market_value),
        )
        ctx.trade_dates.append(bar.trade_date)
        return ctx


class FullBacktestStep(PipelineStep[TradingContext]):
    """Record full-backtest per-timing facts for final aggregation."""

    stage = "full_backtest"

    def run(self, ctx: TradingContext) -> TradingContext:
        bar = ctx.require_current_bar()
        ctx.full_backtest_frames.append(
            {
                **full_backtest_frame(
                    equity_points=ctx.equity_curve.points,
                    positions=ctx.portfolio_state.positions,
                ),
                "trade_date": bar.trade_date,
                "ts_us": int(bar.ts_us),
            }
        )
        logs.info(
            f"[FullBacktestStep] processed trade_date={bar.trade_date} "
            f"symbols={len(bar.symbols)} bars={ctx.current_bars_count} "
            f"positions={len(ctx.portfolio_state.positions)} "
            f"ledger_records={len(ctx.execution_ledger.records)}"
        )
        return ctx


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

    def get_phase(self, symbol: str) -> int | None:
        self._require_active()
        if str(symbol) in self._prices:
            return TRADING
        try:
            return self._base.get_phase(symbol)
        except KeyError:
            return None

    def get_price(self, symbol: str) -> float | None:
        self._require_active()
        value = self._prices.get(str(symbol))
        if value is None:
            return None
        if not math.isfinite(float(value)):
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
