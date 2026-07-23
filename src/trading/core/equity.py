# filepath: src/trading/core/equity.py
from __future__ import annotations

from dataclasses import dataclass, field
import math


# ==========================================================
# EquityPoint (FACT)
# ==========================================================

@dataclass(frozen=True, slots=True)
class EquityPoint:
    """
    Immutable valuation snapshot.

    Invariants:
        equity = cash + market_value
        peak_equity >= equity
        drawdown <= 0
    """

    ts_us: int
    cash: float
    market_value: float
    equity: float
    peak_equity: float
    drawdown: float


# ==========================================================
# EquityCurve
# ==========================================================

@dataclass(slots=True)
class EquityCurve:
    """Maintain a timestamp-ordered equity and drawdown history.

    Guarantees:
        - equity = cash + market_value
        - monotonic time
        - same ts_us -> replace
        - O(1) peak tracking
        - O(1) drawdown tracking
    """

    points: list[EquityPoint] = field(default_factory=list)

    # internal rolling state (NOT exposed)
    _peak_equity: float = 0.0
    _max_drawdown: float = 0.0

    # ---------------------------------------------------------
    # Write Interface
    # ---------------------------------------------------------

    def append(self, *, ts_us: int, cash: float, market_value: float) -> None:
        ts_us = int(ts_us)
        cash = float(cash)
        market_value = float(market_value)
        if not math.isfinite(cash) or not math.isfinite(market_value):
            raise ValueError("cash and market_value must be finite")

        equity = cash + market_value

        if self.points:
            last_ts = int(self.points[-1].ts_us)
            if ts_us < last_ts:
                raise ValueError(
                    f"EquityCurve time regression: {ts_us} < {last_ts}"
                )

        replacing = bool(self.points and self.points[-1].ts_us == ts_us)
        historical_points = self.points[:-1] if replacing else self.points
        if not historical_points:
            peak = equity
        else:
            peak = max(historical_points[-1].peak_equity, equity)

        if peak == 0:
            drawdown = 0.0
        else:
            drawdown = (equity - peak) / peak

        if peak < equity:
            raise RuntimeError("Peak invariant violated")

        if drawdown > 1e-12:
            raise RuntimeError("Drawdown invariant violated")

        p = EquityPoint(
            ts_us=ts_us,
            cash=cash,
            market_value=market_value,
            equity=equity,
            peak_equity=peak,
            drawdown=drawdown,
        )

        if replacing:
            self.points[-1] = p
        else:
            self.points.append(p)

        self._peak_equity = peak
        historical_max_drawdown = min(
            (point.drawdown for point in historical_points),
            default=0.0,
        )
        self._max_drawdown = min(historical_max_drawdown, drawdown)

    # ---------------------------------------------------------
    # Read-only API
    # ---------------------------------------------------------

    def equity_series(self) -> list[float]:
        return [float(p.equity) for p in self.points]

    def ts_series(self) -> list[int]:
        return [int(p.ts_us) for p in self.points]

    def drawdown_series(self) -> list[float]:
        return [float(p.drawdown) for p in self.points]

    @property
    def peak_equity(self) -> float:
        return float(self._peak_equity)

    @property
    def max_drawdown(self) -> float:
        return float(self._max_drawdown)

    @property
    def latest(self) -> EquityPoint | None:
        return self.points[-1] if self.points else None
