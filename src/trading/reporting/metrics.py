# filepath: src/trading/reporting/metrics.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape
from src.trading.portfolio.state import PortfolioState
from src.trading.reporting.attribution import AttributionReport
from src.trading.reporting.behavior import BehaviorReport
from src.trading.reporting.costs import CostReport
from src.trading.reporting.performance import PerformanceReport


def build_backtest_metrics(
    *,
    state: PortfolioState,
    ledger: ExecutionLedger,
    equity: EquityCurve,
    signal_tape: SignalTape,
    target_tape: TargetTape,
    trade_dates: Sequence[str],
) -> dict[str, object]:
    """Build the formal backtest metrics JSON object from runtime facts."""
    if not trade_dates:
        raise RuntimeError("[backtest_metrics] trade_dates required")

    latest = equity.latest
    metrics: dict[str, object] = {
        "trade_days": len(trade_dates),
        "start_date": str(trade_dates[0]),
        "end_date": str(trade_dates[-1]),
        "final_cash": float(state.cash),
        "final_market_value": float(latest.market_value if latest else 0.0),
        "final_equity": float(latest.equity if latest else state.cash),
        "final_positions": dict(state.positions),
    }
    metrics.update(PerformanceReport(equity=equity).summary(annual_factor=252.0))
    metrics.update(BehaviorReport(ledger=ledger, equity=equity).summary())
    metrics.update(CostReport(ledger=ledger).summary())
    metrics.update(
        AttributionReport(
            signal_tape=signal_tape,
            target_tape=target_tape,
            ledger=ledger,
        ).summary()
    )
    safe_metrics = _json_safe_metrics(metrics)
    if not isinstance(safe_metrics, dict):
        raise RuntimeError("backtest metrics must remain a JSON object")
    return safe_metrics


def _json_safe_metrics(value: object) -> object:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _json_safe_metrics(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_metrics(v) for v in value]
    return value
