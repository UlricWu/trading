# filepath: src/trading/risk/rules/max_position.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from src.trading.risk.base import RiskContext, RiskDecision, TargetPositions, RiskRule


def _notional(
    *,
    positions: TargetPositions,
    prices: Mapping[str, float],
) -> float:
    tot = 0.0
    for sym, qty in positions.items():
        px = float(prices.get(sym, 0.0))
        tot += abs(float(qty)) * px
    return float(tot)


@dataclass(frozen=True, slots=True)
class MaxSingleNameNotionalRule(RiskRule):
    """
    Soft risk:
    Cap per-symbol notional exposure by a ratio of equity.

    max_ratio = 0.2  ->  |qty|*price <= 0.2 * equity
    """

    max_ratio: float
    name: str = "max_single_name"

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_ratio) or self.max_ratio < 0.0:
            raise ValueError("max_ratio must be finite and non-negative")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:

        if ctx.equity <= 0:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="equity<=0",
            )

        cap = float(self.max_ratio) * ctx.equity
        out: dict[str, float] = dict(target)

        scaled_flag = False

        for sym, qty in list(out.items()):
            px = float(ctx.prices.get(sym, 0.0))
            if px <= 0:
                continue

            notional = abs(float(qty)) * px
            if notional <= cap:
                continue

            scale = cap / notional if notional > 0 else 0.0
            out[sym] = float(qty) * scale
            scaled_flag = True

        return RiskDecision(
            adjusted=out,
            blocked=False,
            scaled=scaled_flag,
            reason=f"cap={cap:.2f}",
        )


@dataclass(frozen=True, slots=True)
class MaxGrossExposureRule(RiskRule):
    """
    Soft risk:
    Cap total gross notional exposure by a ratio of equity.

    max_gross_ratio=0.5 -> sum(|qty|*price) <= 0.5*equity
    """

    max_gross_ratio: float
    name: str = "max_gross_exposure"

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_gross_ratio) or self.max_gross_ratio < 0.0:
            raise ValueError("max_gross_ratio must be finite and non-negative")

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:

        if ctx.equity <= 0:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="equity<=0",
            )

        gross = _notional(positions=target, prices=ctx.prices)
        cap = float(self.max_gross_ratio) * ctx.equity

        if gross <= cap or gross <= 0:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="pass",
            )

        scale = cap / gross
        out = {sym: float(qty) * scale for sym, qty in target.items()}

        return RiskDecision(
            adjusted=out,
            blocked=False,
            scaled=True,
            reason=f"scaled={scale:.4f}",
        )
