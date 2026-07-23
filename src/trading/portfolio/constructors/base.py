# filepath: src/trading/portfolio/constructors/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from src.trading.portfolio.state import PortfolioState


class PortfolioConstructor(ABC):
    """
    Portfolio-construction contract.

    Contract:
    - consumes scores (signal facts)
    - outputs ideal targets (symbol -> shares)
    - must NOT enforce institutional rules (T+1/limits/cash)
    """
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def targets(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        state: PortfolioState,
    ) -> dict[str, int]:
        ...
