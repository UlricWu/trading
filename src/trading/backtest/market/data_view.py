# filepath: src/trading/backtest/market/data_view.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import numpy as np

from pydantic import ConfigDict

from src.trading.core.time import BarTime


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """A complete, unit-normalized execution observation.

    None volume explicitly means no volume cap for reference execution only.

    Example:
        observation = ExecutionObservation("minute-1", "600000", bar_time, Decimal("10"), 300)
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    observation_id: str
    symbol: str
    time: BarTime
    price: Decimal
    volume_qty: int | None

    def __post_init__(self) -> None:
        if not self.observation_id or not self.symbol:
            raise ValueError("observation_id and symbol must be nonempty")
        price = Decimal(str(self.price))
        if not price.is_finite() or price <= 0:
            raise ValueError("observation price must be finite and positive")
        object.__setattr__(self, "price", price)
        if self.volume_qty is not None and (type(self.volume_qty) is not int or self.volume_qty < 0):
            raise ValueError("volume_qty must be a non-negative integer or None")


class PriceView(Protocol):
    """The current observable raw price required by order preparation.

    Example:
        price = view.get_price("600000")
    """

    def get_price(self, symbol: str) -> Decimal | float | None:
        """Return a visible price or an ordinary missing observation.

        Example:
            price = view.get_price("600000")
        """
        ...


class MarketDataView(Protocol):
    """MarketDataView fact query interface.

    Contract:
    - Answers ONLY observable facts at current ts_us.
    - Must be driven by on_time(ts_us) before queries.
    - Must NOT infer future facts or reconstruct missing data.

    Example:
        view: MarketDataView = DailyView(
            pd.DataFrame({"symbol": ["600000"], "adjusted_close": [10.0]}),
            trade_date="2026-07-27",
        )
        view.on_time(view.bar_timestamps_us()[0])
        price = view.get_price("600000")
    """

    def on_time(self, ts_us: int) -> None:
        ...

    def time_bounds_us(self) -> tuple[int, int]:
        ...

    def bar_timestamps_us(self) -> list[int]:
        ...

    def get_price(self, symbol: str) -> float | None:
        ...

    def get_feature_matrix(self, symbols: Sequence[str]) -> np.ndarray:
        """
        Returns:
            shape (n_symbols, n_features)

        Must follow symbol order of input list.
        """
        ...

    def get_price_vector(self, symbols: Sequence[str]) -> np.ndarray:
        ...

    @property
    def frequency(self) -> str:
        ...

    @property
    def symbols(self) -> list[str]:
        ...

    @property
    def trade_date(self) -> str:
        ...
