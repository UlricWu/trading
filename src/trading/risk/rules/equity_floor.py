# filepath: src/trading/risk/rules/equity_floor.py
from __future__ import annotations

from dataclasses import dataclass
import math

from src.trading.risk.base import RiskContext, RiskDecision, TargetPositions, RiskRule


@dataclass(frozen=True, slots=True)
class EquityFloorRule(RiskRule):
    """
    Hard risk: if equity falls below a floor ratio of peak equity, block all positions.
    Equivalent to "stop trading / force flat".

    Example:
        floor_ratio=0.8  -> if equity <= 0.8 * peak_equity => block.
    """
    floor_ratio: float
    name: str = "equity_floor"

    def __post_init__(self) -> None:
        if not math.isfinite(self.floor_ratio) or not 0.0 <= self.floor_ratio <= 1.0:
            raise ValueError("floor_ratio must be finite and between 0 and 1")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:

        if ctx.peak_equity <= 0:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="peak<=0",
            )

        threshold = ctx.peak_equity * float(self.floor_ratio)

        if ctx.equity <= threshold:
            return RiskDecision(
                adjusted={str(symbol): 0 for symbol in ctx.positions},
                blocked=True,
                scaled=False,
                reason=f"equity<=floor({threshold:.2f})",
            )

        return RiskDecision(
            adjusted=dict(target),
            blocked=False,
            scaled=False,
            reason="pass",
        )
