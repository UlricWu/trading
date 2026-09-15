# filepath: src/trading/risk/rules/max_drawdown.py
from __future__ import annotations

from dataclasses import dataclass
import math

from src.trading.risk.base import RiskContext, RiskDecision, TargetPositions, RiskRule


@dataclass(frozen=True, slots=True)
class MaxDrawdownKillSwitchRule(RiskRule):
    """
    Hard risk: if drawdown exceeds limit, block all trading (flat).

    drawdown = 1 - equity / peak_equity
    """
    max_drawdown: float
    name: str = "max_drawdown_kill"

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.max_drawdown)
            or not 0.0 <= self.max_drawdown <= 1.0
        ):
            raise ValueError("max_drawdown must be finite and between 0 and 1")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:

        if ctx.peak_equity <= 0:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="peak<=0",
            )

        dd = 1.0 - (ctx.equity / ctx.peak_equity)

        if dd >= float(self.max_drawdown):
            return RiskDecision(
                adjusted={str(symbol): 0 for symbol in ctx.positions},
                blocked=True,
                scaled=False,
                reason=f"dd>={self.max_drawdown:.2%}",
            )

        return RiskDecision(
            adjusted=dict(target),
            blocked=False,
            scaled=False,
            reason="pass",
        )
