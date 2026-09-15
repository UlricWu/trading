# filepath: src/trading/risk/engine.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

from src.trading.risk.base import (
    RiskContext,
    RiskDecision,
    RiskRule,
    TargetPositions,
)


def _normalize_adjusted_targets(target: TargetPositions) -> dict[str, int]:
    out: dict[str, int] = {}
    for symbol, qty in target.items():
        if isinstance(qty, bool) or not isinstance(qty, int | float):
            raise RuntimeError(f"[RiskManager] invalid target qty: {symbol}={qty}")
        value = float(qty)
        if not math.isfinite(value):
            raise RuntimeError(f"[RiskManager] invalid target qty: {symbol}={qty}")
        if value < 0.0:
            raise RuntimeError(f"[RiskManager] negative target qty: {symbol}={qty}")
        out[str(symbol)] = int(value)
    return out


@dataclass(frozen=True, slots=True)
class RiskManager:
    """
    Evaluate configured risk rules in deterministic order.

    Semantics:
        - Rules applied sequentially
        - Each rule receives the CURRENT adjusted target
        - Rule may:
              • scale
              • block
              • generate new target
        - Hard block stops chain
        - scaled flag accumulates
    """

    rules: Sequence[RiskRule]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:
        cur: TargetPositions = dict(target)

        scaled_any = False
        last_reason = "ok"

        for rule in self.rules:

            decision = rule.apply(
                target=dict(cur),   # pass copy (pure semantics)
                ctx=ctx,
            )

            cur = dict(decision.adjusted)

            if decision.scaled:
                scaled_any = True

            last_reason = f"{rule.name}:{decision.reason}"

            if decision.blocked:
                return RiskDecision(
                    adjusted=_normalize_adjusted_targets(cur),
                    blocked=True,
                    scaled=scaled_any,
                    reason=last_reason,
                )

        return RiskDecision(
            adjusted=_normalize_adjusted_targets(cur),
            blocked=False,
            scaled=scaled_any,
            reason=last_reason,
        )


@dataclass(frozen=True, slots=True)
class NoOpRiskManager:
    """
    NoOpRiskManager

    Fully bypasses risk layer.
    """

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:
        return RiskDecision(
            adjusted=_normalize_adjusted_targets(target),
            blocked=False,
            scaled=False,
            reason="noop",
        )
