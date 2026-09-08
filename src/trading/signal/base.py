# filepath: src/trading/signal/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.trading.market.data_view import MarketDataView


class SignalProvider(ABC):
    """
    Signal provider contract.

    Contract:
    - produces scores only (symbol -> float)
    - must NOT output targets
    - must NOT be aware of execution constraints (T+1/limits/cash)
    """

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def scores(
        self,
        *,
        ts_us: int,
        data_view: MarketDataView,
        symbols: Sequence[str],
    ) -> dict[str, float]:
        ...
