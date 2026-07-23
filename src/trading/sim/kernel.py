# filepath: src/trading/sim/kernel.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from src.pipeline.phase import TRADING
from src.trading.core.time import ReplayClock, is_minute_boundary
from src.trading.market.data_view import MarketDataView


@dataclass(frozen=True, slots=True)
class BarContext:
    ts_us: int
    data_view: MarketDataView
    symbols: tuple[str, ...]
    phase: object | None
    should_trade: bool
    frequency: str
    trade_date: str


class TradeGate(Protocol):
    def should_trade(
        self,
        *,
        ts_us: int,
        phase: object | None,
        data_view: MarketDataView,
    ) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class FrequencyTradeGate:
    """Default trade gate for replay bars."""

    def should_trade(
        self,
        *,
        ts_us: int,
        phase: object | None,
        data_view: MarketDataView,
    ) -> bool:
        if phase != TRADING:
            return False

        frequency = str(data_view.frequency or "").lower().strip()
        if frequency == "minute":
            return is_minute_boundary(ts_us)
        if frequency in {"daily", "day"}:
            return True

        raise ValueError(f"unsupported data_view frequency for trade gate: {frequency}")


BarHandler = Callable[[BarContext], None]


@dataclass(frozen=True, slots=True)
class BacktestKernel:
    """Frequency-neutral replay loop."""

    clock: ReplayClock
    data_view: MarketDataView
    on_bar: BarHandler | None = None
    trade_gate: TradeGate = field(default_factory=FrequencyTradeGate)

    def run(self) -> list[BarContext]:
        bars: list[BarContext] = []
        last_ts: int | None = None

        for raw_ts in self.clock:
            ts_us = int(raw_ts)
            if last_ts is not None and ts_us <= last_ts:
                raise ValueError("BacktestKernel clock must be strictly increasing")
            last_ts = ts_us

            self.data_view.on_time(ts_us)
            symbols = tuple(str(symbol) for symbol in self.data_view.symbols)
            phase = self._phase(symbols)
            should_trade = self.trade_gate.should_trade(
                ts_us=ts_us,
                phase=phase,
                data_view=self.data_view,
            )
            ctx = BarContext(
                ts_us=ts_us,
                data_view=self.data_view,
                symbols=symbols,
                phase=phase,
                should_trade=should_trade,
                frequency=str(self.data_view.frequency),
                trade_date=self.data_view.trade_date,
            )
            bars.append(ctx)
            if self.on_bar is not None:
                self.on_bar(ctx)

        return bars

    def _phase(self, symbols: tuple[str, ...]) -> object | None:
        if not symbols:
            return None
        return self.data_view.get_phase(symbols[0])
