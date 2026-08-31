# filepath: src/trading/reporting/performance.py
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from src.trading.core.equity import EquityCurve
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class PeriodStats:
    start_date: str
    end_date: str
    calendar_days: int
    trade_days: int
    bars: int


@dataclass(frozen=True, slots=True)
class DailyStats:
    avg_daily_return: float
    daily_vol: float


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """
    Compute performance metrics from an equity curve.

    Reads ONLY:
    - EquityCurve

    Produces:
    - total_return
    - max_drawdown
    - sharpe (annualized by frequency if provided)
    - period stats
    - daily stats
    - annual_return (calendar-based)
    - risk ratios
    """

    equity: EquityCurve

    # ---------------------------
    # Core series
    # ---------------------------

    def _equity_df(self) -> pd.DataFrame:
        pts = self.equity.points
        df = pd.DataFrame([{"ts_us": p.ts_us, "equity": float(p.equity)} for p in pts])
        if df.empty:
            return df
        df["dt"] = [
            DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(int(ts_us)))
            for ts_us in df["ts_us"]
        ]
        return df

    # ---------------------------
    # Metrics
    # ---------------------------

    def total_return(self) -> float:
        pts = self.equity.points
        if len(pts) < 2:
            return 0.0
        s = float(pts[0].equity)
        e = float(pts[-1].equity)
        return (e - s) / s if s > 0 else 0.0

    def max_drawdown(self) -> float:
        return abs(float(self.equity.max_drawdown))

    def sharpe(self, *, annual_factor: float) -> float:
        pts = self.equity.points
        if len(pts) < 3:
            return 0.0

        rets: list[float] = []
        for i in range(1, len(pts)):
            prev = float(pts[i - 1].equity)
            cur = float(pts[i].equity)
            if prev > 0:
                rets.append((cur - prev) / prev)

        if len(rets) < 2:
            return 0.0

        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        return (mean / std) * math.sqrt(annual_factor) if std > 0 else 0.0

    # ---------------------------
    # Period/Daily derived
    # ---------------------------

    def period_stats(self) -> PeriodStats:
        df = self._equity_df()
        if df.empty:
            return PeriodStats("NA", "NA", 0, 0, 0)

        start_dt = df["dt"].iloc[0]
        end_dt = df["dt"].iloc[-1]
        calendar_days = (end_dt.date() - start_dt.date()).days + 1
        trade_days = len(set(df["dt"].dt.date))
        bars = len(df)
        return PeriodStats(
            start_date=str(start_dt.date()),
            end_date=str(end_dt.date()),
            calendar_days=int(calendar_days),
            trade_days=int(trade_days),
            bars=int(bars),
        )

    def daily_stats(self) -> DailyStats:
        df = self._equity_df()
        if df.empty:
            return DailyStats(0.0, 0.0)

        daily_eq = df.set_index("dt").resample("1D")["equity"].last().dropna()

        if len(daily_eq) < 2:
            return DailyStats(0.0, 0.0)

        daily_rets = daily_eq.pct_change().dropna()
        if daily_rets.empty:
            return DailyStats(0.0, 0.0)

        return DailyStats(
            avg_daily_return=float(daily_rets.mean()),
            daily_vol=float(daily_rets.std(ddof=1)),
        )

    def annual_return_from_calendar(self) -> float:
        ps = self.period_stats()
        tr = self.total_return()
        if ps.calendar_days <= 0:
            return 0.0
        return (1.0 + tr) ** (365.0 / ps.calendar_days) - 1.0

    # ---------------------------
    # Summary
    # ---------------------------

    def summary(self, *, annual_factor: float) -> dict[str, float]:
        tr = float(self.total_return())
        mdd = float(self.max_drawdown())
        ann = float(self.annual_return_from_calendar())
        ds = self.daily_stats()

        out: dict[str, float] = {
            "total_return": tr,
            "max_drawdown": mdd,
            "annual_return": ann,
            "cagr": ann,
            "avg_daily_return": float(ds.avg_daily_return),
            "daily_vol": float(ds.daily_vol),
            "risk_return_ratio": float(tr / mdd) if mdd > 0 else 0.0,
            "sharpe": float(self.sharpe(annual_factor=annual_factor)),
        }
        return out
