# filepath: src/trading/portfolio/constructors/threshold.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.state import PortfolioState


@dataclass(frozen=True, slots=True)
class ThresholdConstructor(PortfolioConstructor):
    """
    Select signals at or above a fixed threshold.

    Semantics:
    - if score >= threshold -> target qty
    - else -> 0
    """
    threshold: float
    target_quantity: int = 100

    def name(self) -> str:
        """Return the public constructor type."""
        return "threshold"

    def targets(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        state: PortfolioState,
    ) -> dict[str, int]:
        """Convert finite scores into fixed-quantity targets."""
        out: dict[str, int] = {}
        for symbol, score in scores.items():
            score_value = float(score)
            if not math.isfinite(score_value):
                raise ValueError(f"score must be finite: {symbol}={score!r}")
            out[str(symbol)] = (
                self.target_quantity if score_value >= self.threshold else 0
            )
        return out
