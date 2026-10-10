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

    Example:
        curve = EquityCurve()
        curve.append(ts_us=1, cash=20_000.0, market_value=0.0)
    """

    points: list[EquityPoint] = field(default_factory=list)
    starting_peak_equity: float = 0.0

    # internal rolling state (NOT exposed)
    _peak_equity: float = 0.0
    _max_drawdown: float = 0.0
    _drawdown_minima: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.points = list(self.points)
        for point in self.points:
            self._max_drawdown = min(self._max_drawdown, point.drawdown)
            self._drawdown_minima.append(self._max_drawdown)
            self._peak_equity = point.peak_equity

    # ---------------------------------------------------------
    # Write Interface
    # ---------------------------------------------------------

    def append(self, *, ts_us: int, cash: float, market_value: float) -> None:
        """Append or replace a valuation with constant-time rolling statistics.

        Example:
            curve.append(ts_us=1, cash=20_000.0, market_value=0.0)
        """
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
        previous_index = len(self.points) - (2 if replacing else 1)
        if previous_index < 0:
            peak = max(self.starting_peak_equity, equity)
            historical_max_drawdown = 0.0
        else:
            peak = max(self.points[previous_index].peak_equity, equity)
            historical_max_drawdown = self._drawdown_minima[previous_index]

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
            self._drawdown_minima[-1] = min(historical_max_drawdown, drawdown)
        else:
            self.points.append(p)
            self._drawdown_minima.append(min(historical_max_drawdown, drawdown))

        self._peak_equity = peak
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
