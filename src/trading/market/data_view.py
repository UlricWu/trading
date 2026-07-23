# filepath: src/trading/market/data_view.py
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class MarketDataView(Protocol):
    """
    MarketDataView fact query interface.

    Contract:
    - Answers ONLY observable facts at current ts_us.
    - Must be driven by on_time(ts_us) before queries.
    - Must NOT infer future facts or reconstruct missing data.
    """

    def on_time(self, ts_us: int) -> None:
        ...

    def time_bounds_us(self) -> tuple[int, int]:
        ...

    def bar_timestamps_us(self) -> list[int]:
        ...

    def get_phase(self, symbol: str) -> int | None:
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
