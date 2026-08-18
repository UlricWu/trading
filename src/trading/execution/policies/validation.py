# filepath: src/trading/execution/policies/validation.py
from __future__ import annotations

import math
from dataclasses import dataclass

from src.trading.core.events import OrderIntent


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    reason: str = ""
    detail: str | None = None


class AShareOrderValidation:
    """Validate A-share order targets after the trade gate admits a bar.

    Hard constraints:
    - qty positive
    - lot-size compliant
    - BUY cash check
    - SELL long-only + optional T+1 (validator can enforce or rely on clipping policy)
    - price finite/positive

    Example:
        validator = AShareOrderValidation()
        result = validator.validate(
            intent=intent,
            price=10.0,
            cash=10_000.0,
            current_position=0,
            t1_sellable=0,
        )
    """
    def __init__(
        self,
        *,
        lot_size: int = 100,
        enforce_t1: bool = True,
        enforce_cash: bool = True,
    ) -> None:
        if isinstance(lot_size, bool) or not isinstance(lot_size, int):
            raise TypeError("lot_size must be an int")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if not isinstance(enforce_t1, bool) or not isinstance(enforce_cash, bool):
            raise TypeError("enforcement flags must be bool")
        self.lot_size = lot_size
        self.enforce_t1 = enforce_t1
        self.enforce_cash = enforce_cash

    def validate(
        self,
        *,
        intent: OrderIntent,
        price: float | None,
        cash: float,
        current_position: int,
        t1_sellable: int | None,
    ) -> ValidationResult:
        """Return the institutional validation result for one order intent.

        Example:
            result = validator.validate(
                intent=intent,
                price=10.0,
                cash=10_000.0,
                current_position=0,
                t1_sellable=0,
            )
        """
        if isinstance(intent.qty, bool) or not isinstance(intent.qty, int):
            raise TypeError("intent.qty must be an int")
        qty = intent.qty
        if qty <= 0:
            return ValidationResult(ok=False, reason="QTY_NON_POSITIVE")

        if qty % self.lot_size != 0:
            return ValidationResult(ok=False, reason="LOT_SIZE_VIOLATION", detail=f"qty={qty} lot={self.lot_size}")

        if (
            price is None
            or isinstance(price, bool)
            or not isinstance(price, int | float)
            or not math.isfinite(float(price))
            or float(price) <= 0.0
        ):
            return ValidationResult(ok=False, reason="INVALID_PRICE", detail=f"price={price}")

        px = float(price)

        if intent.side.value == "BUY":
            if not math.isfinite(float(cash)):
                raise ValueError("cash must be finite")
            need = px * qty
            if self.enforce_cash and float(cash) + 1e-12 < need:
                return ValidationResult(ok=False, reason="INSUFFICIENT_CASH", detail=f"cash={cash} need={need}")
            return ValidationResult(ok=True)

        if intent.side.value == "SELL":
            if qty > int(current_position):
                return ValidationResult(ok=False, reason="SELL_EXCEEDS_POSITION", detail=f"pos={current_position} sell={qty}")

            if self.enforce_t1:
                if t1_sellable is None:
                    return ValidationResult(ok=False, reason="T1_SELLABLE_UNKNOWN")
                if qty > int(t1_sellable):
                    return ValidationResult(ok=False, reason="T1_VIOLATION", detail=f"sellable={t1_sellable} sell={qty}")

            return ValidationResult(ok=True)

        return ValidationResult(ok=False, reason="UNKNOWN_SIDE", detail=str(intent.side))
