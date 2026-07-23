# filepath: src/trading/reporting/attribution.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.core.ledger import ExecutionLedger
from src.trading.core.tape import SignalTape, TargetTape


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """
    Attribute returns by symbol from ledger facts.

    Reads ONLY:
    - SignalTape
    - TargetTape
    - Ledger

    Produces alignment diagnostics (NO pnl inference).
    """
    signal_tape: SignalTape
    target_tape: TargetTape
    ledger: ExecutionLedger

    def summary(self) -> dict[str, float]:
        return {
            "signal_events": float(len(self.signal_tape.events)),
            "target_events": float(len(self.target_tape.events)),
            "ledger_records": float(len(self.ledger.records)),
        }
