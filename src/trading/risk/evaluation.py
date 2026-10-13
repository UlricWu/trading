# filepath: src/trading/risk/evaluation.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Self

from pydantic import ConfigDict, PlainSerializer, StrictInt, TypeAdapter

from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem

from src.trading.risk.base import RiskContext, RiskDecision
from src.trading.risk.engine import RiskManager


@dataclass(frozen=True, slots=True)
class RiskCase:
    """A frozen pre-risk request; None target is a timer check.

    Example:
        case = RiskCase("timer-1", "2026-07-03", None, context)
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    case_id: str
    trade_date: str
    target: Annotated[Mapping[str, StrictInt], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])] | None
    context: RiskContext

    def __post_init__(self) -> None:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if not self.case_id:
            raise ValueError("risk case_id must be nonempty")
        if self.target is not None:
            object.__setattr__(self, "target", MappingProxyType(dict(self.target)))


@dataclass(frozen=True, slots=True)
class RiskObservation:
    """An exact decision, with an optional post-execution account snapshot.

    Frozen-case evaluation has no outcome; account replay records it after settlement.

    Example:
        observation = RiskObservation(case, risk.apply(target=case.target, ctx=case.context))
    """

    case: RiskCase
    decision: RiskDecision
    outcome: RiskContext | None = None


@dataclass(frozen=True, slots=True)
class RiskCases:
    """Own ordered, uniquely identified frozen inputs for independent risk evaluation.

    Example:
        cases = RiskCases((case,))
        observations = cases.evaluate(risk=risk_manager)
    """

    cases: Sequence[RiskCase]

    def __post_init__(self) -> None:
        cases = tuple(self.cases)
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("risk case_id must be unique")
        object.__setattr__(self, "cases", cases)

    @classmethod
    def from_files(cls, refs: Sequence[Path]) -> Self:
        """Strictly parse case arrays in file order, then establish collection invariants.

        Example:
            cases = RiskCases.from_files((Path("/data/cases.json"),))
        """
        adapter = TypeAdapter(tuple[RiskCase, ...])
        return cls(tuple(case for ref in refs
                         for case in adapter.validate_json(ref.read_bytes(), strict=True)))

    def write(self, ref: Path) -> None:
        """Atomically write the original ordered case array without evaluating rules.

        Example:
            cases.write(Path("/data/risk-cases.json"))
        """
        FileSystem.write_bytes_atomic(ref, TypeAdapter(tuple[RiskCase, ...]).dump_json(self.cases))

    def evaluate(self, *, risk: RiskManager) -> tuple[RiskObservation, ...]:
        """Apply the shared rule chain once per frozen case without changing the inputs.

        Example:
            observations = cases.evaluate(risk=risk_manager)
        """
        return tuple(RiskObservation(case, risk.apply(target=case.target, ctx=case.context)) for case in self.cases)
