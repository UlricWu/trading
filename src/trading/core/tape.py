# filepath: src/trading/core/tape.py
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from src.trading.core.events import SignalEvent, TargetEvent


class RiskTapeRecord(TypedDict):
    ts_us: int
    blocked: bool
    scaled: bool
    reason: str
    positions: dict[str, float]


@dataclass(slots=True)
class SignalTape:
    """
    SignalTape (FACT TAPE)

    Semantics:
    - append-only, replayable
    - keyed by ts_us
    """
    events: list[SignalEvent] = field(default_factory=list)

    def append(self, e: SignalEvent) -> None:
        self.events.append(e)

    def iter(self) -> Iterator[SignalEvent]:
        yield from self.events

    def get(self, ts_us: int) -> SignalEvent | None:
        t = int(ts_us)
        for e in reversed(self.events):
            if int(e.ts_us) == t:
                return e
        return None

    def as_dict(self) -> list[dict[str, object]]:
        return [{"ts_us": int(e.ts_us), "scores": dict(e.scores), "meta": dict(e.meta)} for e in self.events]


@dataclass(slots=True)
class TargetTape:
    """
    TargetTape (FACT TAPE)

    Semantics:
    - append-only, replayable
    - keyed by ts_us
    """
    events: list[TargetEvent] = field(default_factory=list)

    def append(self, e: TargetEvent) -> None:
        self.events.append(e)

    def iter(self) -> Iterator[TargetEvent]:
        yield from self.events

    def get(self, ts_us: int) -> TargetEvent | None:
        t = int(ts_us)
        for e in reversed(self.events):
            if int(e.ts_us) == t:
                return e
        return None

    def as_dict(self) -> list[dict[str, object]]:
        return [{"ts_us": int(e.ts_us), "targets": dict(e.targets), "meta": dict(e.meta)} for e in self.events]


@dataclass(slots=True)
class RiskTape:
    """
    RiskAdjustedTargetPositionEvent tape.

    Each record:
        ts_us, reason, blocked, positions (dict)
    """
    records: list[RiskTapeRecord] = field(default_factory=list)

    def append(
        self,
        *,
        ts_us: int,
        blocked: bool,
        scaled: bool = False,
        reason: str,
        positions: Mapping[str, float],
    ) -> None:
        self.records.append(
            {
                "ts_us": int(ts_us),
                "blocked": bool(blocked),
                "scaled": bool(scaled),
                "reason": str(reason),
                "positions": dict(positions),
            }
        )

    def as_dict(self) -> dict[str, list[object]]:
        if not self.records:
            return {}
        cols = sorted({k for r in self.records for k in r.keys()})
        return {c: [r.get(c) for r in self.records] for c in cols}
