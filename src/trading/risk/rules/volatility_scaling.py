# filepath: src/trading/risk/rules/volatility_scaling.py
from __future__ import annotations

import math

from src.trading.risk.base import RiskContext, RiskDecision, TargetPositions, RiskRule


class VolatilityTargetRule(RiskRule):
    """
    Volatility targeting rule.

    若缺失波动率：
        - 不缩放
        - 不拦截
        - 仅记录 reason
    """

    def __init__(
        self,
        target_vol: float,
        max_scale: float = 2.0,
        name: str = "vol_target",
    ) -> None:
        self.target_vol = float(target_vol)
        self.max_scale = float(max_scale)
        self.name = name
        if not math.isfinite(self.target_vol) or self.target_vol <= 0.0:
            raise ValueError("target_vol must be finite and positive")
        if not math.isfinite(self.max_scale) or self.max_scale <= 0.0:
            raise ValueError("max_scale must be finite and positive")

    def apply(
            self,
            *,
            target: TargetPositions,
            ctx: RiskContext,
    ) -> RiskDecision:

        raw_vol = None if ctx.meta is None else ctx.meta.get("vol")

        if raw_vol is None:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="missing_vol",
            )
        if isinstance(raw_vol, bool) or not isinstance(raw_vol, int | float):
            raise ValueError("risk context vol must be numeric")
        vol = float(raw_vol)
        if not math.isfinite(vol) or vol <= 0.0:
            raise ValueError("risk context vol must be finite and positive")

        scale = min(self.max_scale, self.target_vol / vol)

        if abs(scale - 1.0) < 1e-6:
            return RiskDecision(
                adjusted=dict(target),
                blocked=False,
                scaled=False,
                reason="",
            )

        new_target = {
            sym: int(qty * scale)
            for sym, qty in target.items()
        }

        return RiskDecision(
            adjusted=new_target,
            blocked=False,
            scaled=True,
            reason="scaled",
        )
