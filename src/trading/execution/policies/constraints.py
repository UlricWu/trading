# filepath: src/trading/execution/policies/constraints.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from src.trading.core.events import Side


@dataclass(frozen=True, slots=True)
class ClipResult:
    requested_qty: int
    clipped_qty: int
    reason: str | None = None


class AShareTargetClippingPolicy:
    """
    Clip A-share targets to executable quantities.

    Responsibilities:
    - clips execution intent qty using institutional facts:
      T+1, limits, cash, lot-size
    """

    def __init__(
        self,
        *,
        enforce_t1: bool = True,
        enforce_limits: bool = True,
        enforce_cash: bool = True,
        lot_size: int = 100,
        enforce_lot: bool = True,
    ) -> None:
        flags = (enforce_t1, enforce_limits, enforce_cash, enforce_lot)
        if any(not isinstance(flag, bool) for flag in flags):
            raise TypeError("enforcement flags must be bool")
        if isinstance(lot_size, bool) or not isinstance(lot_size, int):
            raise TypeError("lot_size must be an int")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        self.enforce_t1 = enforce_t1
        self.enforce_limits = enforce_limits
        self.enforce_cash = enforce_cash
        self.lot_size = lot_size
        self.enforce_lot = enforce_lot

    def _round_down_lot(self, q: int) -> int:
        lot = max(1, int(self.lot_size))
        if not self.enforce_lot:
            return int(q)
        q = int(q)
        if q <= 0:
            return 0
        return (q // lot) * lot

    def clip_qty(
        self,
        *,
        side: Side,
        requested_qty: int,
        price: float,
        cash: float,
        current_position: int,
        t1_sellable: int,
        limits: Mapping[str, object] | None,
    ) -> ClipResult:
        if isinstance(requested_qty, bool) or not isinstance(requested_qty, int):
            raise TypeError("requested_qty must be an int")
        q_req = requested_qty
        if q_req <= 0:
            return ClipResult(requested_qty=q_req, clipped_qty=0, reason="QTY<=0")

        if isinstance(price, bool) or not isinstance(price, int | float):
            return ClipResult(requested_qty=q_req, clipped_qty=0, reason="INVALID_PRICE")
        px = float(price)
        if not math.isfinite(px) or px <= 0.0:
            return ClipResult(requested_qty=q_req, clipped_qty=0, reason="INVALID_PRICE")

        # limits
        if self.enforce_limits and limits:
            limit_up = _optional_limit_price(limits, "limit_up")
            limit_down = _optional_limit_price(limits, "limit_down")
            if side == Side.BUY and limit_up is not None and px >= limit_up:
                return ClipResult(q_req, 0, "LIMIT_UP")
            if side == Side.SELL and limit_down is not None and px <= limit_down:
                return ClipResult(q_req, 0, "LIMIT_DOWN")

        # -------------------------
        # SELL: position / T+1 clip
        # -------------------------
        if side == Side.SELL:
            cur = max(0, int(current_position))
            q = min(q_req, cur)
            if q <= 0:
                return ClipResult(requested_qty=q_req, clipped_qty=0, reason="NO_POSITION")
            reason = "POSITION_CLIPPED" if q < q_req else None

            if self.enforce_t1:
                sellable = max(0, int(t1_sellable))
                before_t1 = q
                q = min(q, sellable)
                if q <= 0:
                    return ClipResult(requested_qty=q_req, clipped_qty=0, reason="T1_VIOLATION_CLIPPED")
                if q < before_t1:
                    reason = "T1_VIOLATION_CLIPPED"

            q_lot = self._round_down_lot(q)
            if q_lot <= 0:
                return ClipResult(requested_qty=q_req, clipped_qty=0, reason="LOT_ROUNDED_TO_ZERO")
            if q_lot < q:
                reason = "LOT_ROUND_DOWN"
            return ClipResult(requested_qty=q_req, clipped_qty=q_lot, reason=reason)

        # -------------------------
        # BUY: cash clip
        # -------------------------
        q = q_req
        reason = None
        if self.enforce_cash:
            c = float(cash)
            if not math.isfinite(c):
                raise ValueError("cash must be finite")
            max_affordable = int(c // px)
            q = min(q, max_affordable)
            if q <= 0:
                return ClipResult(requested_qty=q_req, clipped_qty=0, reason="INSUFFICIENT_CASH")
            if q < q_req:
                reason = "INSUFFICIENT_CASH"

        q_lot = self._round_down_lot(q)
        if q_lot <= 0:
            return ClipResult(requested_qty=q_req, clipped_qty=0, reason="LOT_ROUNDED_TO_ZERO")

        if q_lot < q:
            reason = "LOT_ROUND_DOWN"

        return ClipResult(requested_qty=q_req, clipped_qty=q_lot, reason=reason)


def _optional_limit_price(
    limits: Mapping[str, object],
    key: str,
) -> float | None:
    value = limits.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric or None")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{key} must be finite and positive")
    return number
