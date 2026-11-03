# filepath: src/trading/risk/rules/max_position.py
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from src.trading.core.events import Side
from src.trading.risk.base import RiskContext, RuleObservation, TargetPositions


@dataclass(frozen=True, slots=True)
class MaxSingleNameNotionalRule:
    """Limit each complete target using the declared quantity step.

    Example:
        rule = MaxSingleNameNotionalRule(max_ratio=0.2)
        observation = rule.apply(target={"600000": 1000}, ctx=context)
    """

    max_ratio: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_ratio) or self.max_ratio < 0:
            raise ValueError("max_ratio must be finite and non-negative")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RuleObservation:
        """Round maximum allowed holdings down without interpreting missing marks as zero.

        Example:
            observation = rule.apply(target=target, ctx=context)
        """
        if ctx.equity <= 0:
            return RuleObservation("max_single_name", target, target, False, "nonpositive_equity", checked=False)
        cap = Decimal(str(self.max_ratio)) * Decimal(str(ctx.equity))
        adjusted = {}
        for symbol, qty in target.items():
            step = ctx.quantity_steps[symbol]
            limit = int(cap / Decimal(str(ctx.prices[symbol])) / step) * step
            adjusted[symbol] = min(qty, limit)
        return RuleObservation("max_single_name", target, adjusted, adjusted != target, "notional_cap")


@dataclass(frozen=True, slots=True)
class MaxGrossExposureRule:
    """Reserve existing holdings before allocating a proportional new-buy budget.

    Example:
        rule = MaxGrossExposureRule(max_gross_ratio=0.8)
        observation = rule.apply(target=target, ctx=context)
    """

    max_gross_ratio: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_gross_ratio) or self.max_gross_ratio < 0:
            raise ValueError("max_gross_ratio must be finite and non-negative")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RuleObservation:
        """Do not release exposure budget for sells that have not filled.

        Example:
            observation = rule.apply(target=target, ctx=context)
        """
        if ctx.equity <= 0:
            return RuleObservation("max_gross_exposure", target, target, False, "nonpositive_equity", checked=False)
        prices = {symbol: Decimal(str(ctx.prices[symbol])) for symbol in target}
        held = sum((qty * prices[symbol] for symbol, qty in ctx.positions.items()), Decimal(0))
        cap = Decimal(str(self.max_gross_ratio)) * Decimal(str(ctx.equity))
        wanted = sum((max(0, qty - ctx.positions.get(symbol, 0)) * prices[symbol]
                      for symbol, qty in target.items()), Decimal(0))
        adjusted: dict[str, int] = {}
        for symbol, qty in target.items():
            position = ctx.positions.get(symbol, 0)
            step = ctx.quantity_steps[symbol]
            if held > cap:
                allowed = int(Decimal(position) * cap / held / step) * step
            else:
                scale = min(Decimal(1), (cap - held) / wanted) if wanted else Decimal(1)
                allowed = position + int(Decimal(max(0, qty - position)) * scale / step) * step
            adjusted[symbol] = min(qty, allowed)
        pending_exposure = sum((p.remaining_qty * prices[p.intent.symbol]
            for p in ctx.pending_orders if p.intent.side == Side.BUY), Decimal(0))
        return RuleObservation("max_gross_exposure", target, adjusted,
            adjusted != target or held + pending_exposure > cap, "holdings_plus_buys")
