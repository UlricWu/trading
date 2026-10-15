# filepath: tests/conftest.py
from collections.abc import Callable

import pytest

from src.config.backtest_config import BacktestMode


@pytest.fixture
def backtest_payload() -> Callable[[BacktestMode | str], dict[str, object]]:
    def build(mode: BacktestMode | str) -> dict[str, object]:
        mode = BacktestMode(mode)
        payload = {"kind": "backtest", "mode": mode.value}
        risk = {"rules": [{"type": "max_single_name", "max_ratio": 0.2}]}
        payload.update(start="2026-07-01", end="2026-07-20")
        if mode != BacktestMode.EXECUTION_EVAL:
            payload["signal"] = {"type": "model", "model_ref": "/data/models/training-1.joblib"}
            payload["min_listing_calendar_days"] = 120
        rule = {"buy_min_qty": 100, "buy_step_qty": 100, "sell_min_qty": 1, "sell_step_qty": 1,
                "fill_step_qty": 1, "price_tick": "0.01", "enforce_t1": True}
        execution = {"market_source": {"frequency": "daily", "start": "2026-07-01",
                                       "end": "2026-07-20", "symbols": ["600000"]}, "end_ts_us": 2,
                     "quantity_rules": {"600000": rule}, "annual_factor": 252, "fill": {"type": "quoted"},
                     "fees": {"commission_rate": 0, "stamp_tax_rate": 0, "transfer_rate": 0, "min_commission": 0}}
        payload.update(initial_account={"ts_us": 1, "trade_date": "2026-07-01", "cash": "20000",
            "positions": {}, "sellable": {}, "average_cost": {}, "peak_equity": 20000}, execution=execution)
        if mode in (BacktestMode.EXECUTION_EVAL, BacktestMode.FULL_BACKTEST):
            execution["fill"] = {"type": "limit_price", "slippage_bp": 0}
        if mode == BacktestMode.EXECUTION_EVAL:
            payload["order_refs"] = ["/data/orders.json"]
        else:
            payload["strategy"] = {"type": "threshold", "params": {"threshold": 0.5}, "order_window_minutes": 2,
                                   "quote": {"buy_offset_bp": 0, "sell_offset_bp": 0}}
        if mode in (BacktestMode.FULL_BACKTEST, BacktestMode.RISK_RULE_EVAL):
            payload["risk"] = risk
        return payload
    return build
