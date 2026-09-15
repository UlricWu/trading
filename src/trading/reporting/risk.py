# filepath: src/trading/reporting/risk.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.core.tape import RiskTape


@dataclass(frozen=True, slots=True)
class RiskReport:
    risk_tape: RiskTape

    def summary(self) -> dict[str, float]:
        records = getattr(self.risk_tape, "records", [])

        if not records:
            return {
                "risk_events": 0,
                "risk_blocked": 0,
                "risk_scaled": 0,
                "risk_kill_switch": 0,
            }

        events = len(records)
        blocked = sum(1 for r in records if r.get("blocked"))
        scaled = sum(1 for r in records if r.get("scaled"))

        kill_switch = sum(
            1 for r in records
            if r.get("reason", "").startswith("max_drawdown")
        )

        return {
            "risk_events": events,
            "risk_blocked": blocked,
            "risk_scaled": scaled,
            "risk_kill_switch": kill_switch,
        }
