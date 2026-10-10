# filepath: src/trading/execution/models/slippage_fixed_bp.py

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FixedBPSlippageModel:
    """
    Fixed basis point slippage model.

    bp = 5 means 5 basis points = 0.05% = 0.0005

    Example:
        model = FixedBPSlippageModel(bp=5)
        price = model.apply(side="BUY", price=Decimal("10"))
    """
    bp: Decimal | float

    def __post_init__(self) -> None:
        bp = Decimal(str(self.bp))
        if not bp.is_finite() or not 0 <= bp < 10_000:
            raise ValueError("bp must be finite and in [0, 10000)")

    def apply(self, *, side: str, price: Decimal | float) -> Decimal:
        """Apply the declared offset without binary currency arithmetic.

        Example:
            price = FixedBPSlippageModel(bp=5).apply(side="BUY", price=Decimal("10"))
        """
        price = Decimal(str(price))
        if not price.is_finite() or price <= 0:
            raise ValueError(f"Invalid price for slippage: {price}")

        slip = Decimal(str(self.bp)) / 10_000

        if side == "BUY":
            exec_px = price * (1 + slip)
        elif side == "SELL":
            exec_px = price * (1 - slip)
        else:
            raise ValueError(f"Unknown side: {side}")

        if exec_px <= 0:
            raise RuntimeError(
                f"Slippage generated non-positive price. "
                f"price={price}, bp={self.bp}, exec_px={exec_px}"
            )

        return exec_px
