# filepath: src/trading/sim/kernel.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from src.trading.core.time import ReplayClock, is_minute_boundary
from src.trading.market.data_view import MarketDataView


@dataclass(frozen=True, slots=True)
class BarContext:
    """Describe one replayed bar and its execution permission.

    Example:
        bar = BarContext(
            ts_us=1,
            data_view=view,
            symbols=("600000",),
            should_trade=True,
            frequency="daily",
            trade_date="2026-07-27",
        )
    """

    ts_us: int
    data_view: MarketDataView
    symbols: tuple[str, ...]
    should_trade: bool
    frequency: str
    trade_date: str


class TradeGate(Protocol):
    """Decide whether one replay bar may execute.

    Example:
        gate: TradeGate = FrequencyTradeGate()
        allowed = gate.should_trade(ts_us=1, data_view=view)
    """

    def should_trade(
        self,
        *,
        ts_us: int,
        data_view: MarketDataView,
    ) -> bool:
        """Return whether execution is allowed for the replay bar.

        Example:
            allowed = gate.should_trade(ts_us=1, data_view=view)
        """
        ...


@dataclass(frozen=True, slots=True)
class FrequencyTradeGate:
    """Allow daily bars and real minute boundaries.

    Example:
        gate = FrequencyTradeGate()
        allowed = gate.should_trade(ts_us=1, data_view=daily_view)
    """

    def should_trade(
        self,
        *,
        ts_us: int,
        data_view: MarketDataView,
    ) -> bool:
        """Return the frequency-based execution permission.

        Example:
            allowed = FrequencyTradeGate().should_trade(
                ts_us=1,
                data_view=daily_view,
            )
        """

        frequency = str(data_view.frequency or "").lower().strip()
        if frequency == "minute":
            return is_minute_boundary(ts_us)
        if frequency in {"daily", "day"}:
            return True

        raise ValueError(f"unsupported data_view frequency for trade gate: {frequency}")


BarHandler = Callable[[BarContext], None]


@dataclass(frozen=True, slots=True)
class BacktestKernel:
    """Run a data view over its supplied replay clock.

    Example:
        kernel = BacktestKernel(clock=clock, data_view=view)
        bars = kernel.run()
    """

    clock: ReplayClock
    data_view: MarketDataView
    on_bar: BarHandler | None = None
    trade_gate: TradeGate = field(default_factory=FrequencyTradeGate)

    def run(self) -> list[BarContext]:
        """Return every replayed bar in clock order.

        Example:
            bars = BacktestKernel(clock=clock, data_view=view).run()
        """
        bars: list[BarContext] = []
        last_ts: int | None = None

        for raw_ts in self.clock:
            ts_us = int(raw_ts)
            if last_ts is not None and ts_us <= last_ts:
                raise ValueError("BacktestKernel clock must be strictly increasing")
            last_ts = ts_us

            self.data_view.on_time(ts_us)
            symbols = tuple(str(symbol) for symbol in self.data_view.symbols)
            should_trade = self.trade_gate.should_trade(
                ts_us=ts_us,
                data_view=self.data_view,
            )
            ctx = BarContext(
                ts_us=ts_us,
                data_view=self.data_view,
                symbols=symbols,
                should_trade=should_trade,
                frequency=str(self.data_view.frequency),
                trade_date=self.data_view.trade_date,
            )
            bars.append(ctx)
            if self.on_bar is not None:
                self.on_bar(ctx)

        return bars
