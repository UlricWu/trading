# filepath: src/trading/execution/models/slippage_fixed_bp.py

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FixedBPSlippageModel:
    """
    Fixed basis point slippage model.

    bp = 5 means 5 basis points = 0.05% = 0.0005
    """
    bp: float  # in basis points

    def apply(self, *, side: str, price: float) -> float:
        if price <= 0:
            raise ValueError(f"Invalid price for slippage: {price}")

        slip = float(self.bp) * 1e-4  # convert bp → ratio

        if side == "BUY":
            exec_px = price * (1.0 + slip)
        elif side == "SELL":
            exec_px = price * (1.0 - slip)
        else:
            raise ValueError(f"Unknown side: {side}")

        if exec_px <= 0:
            raise RuntimeError(
                f"Slippage generated non-positive price. "
                f"price={price}, bp={self.bp}, exec_px={exec_px}"
            )

        return float(exec_px)
