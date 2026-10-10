# filepath: src/trading/risk/rules/max_drawdown.py
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from src.trading.risk.base import RiskContext, RuleObservation, TargetPositions


@dataclass(frozen=True, slots=True)
class MaxDrawdownRule:
    """Request a zero target at the threshold without claiming the liquidation filled.

    Example:
        rule = MaxDrawdownRule(max_drawdown=0.1)
        observation = rule.apply(target=target, ctx=context)
    """

    max_drawdown: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_drawdown) or not 0 <= self.max_drawdown <= 1:
            raise ValueError("max_drawdown must be in [0, 1]")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RuleObservation:
        """Evaluate the current snapshot; no persistent freeze is introduced.

        Example:
            observation = rule.apply(target=target, ctx=context)
        """
        if ctx.peak_equity <= 0:
            return RuleObservation("max_drawdown", target, target, False, "nonpositive_peak_equity", checked=False)
        peak = Decimal(str(ctx.peak_equity))
        triggered = peak - Decimal(str(ctx.equity)) >= Decimal(str(self.max_drawdown)) * peak
        adjusted = {symbol: 0 for symbol in target} if triggered else dict(target)
        return RuleObservation("max_drawdown", target, adjusted, triggered, "liquidate" if triggered else "pass")
