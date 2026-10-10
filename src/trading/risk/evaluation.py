# filepath: src/trading/risk/evaluation.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated

from pydantic import ConfigDict, PlainSerializer, StrictInt, TypeAdapter

from src.utils.datetime_utils import DateTimeUtils

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
    """The exact decision produced by a frozen case and the selected risk chain.

    Example:
        observation = RiskObservation(case, risk.apply(target=case.target, ctx=case.context))
    """

    case: RiskCase
    decision: RiskDecision


def read_risk_cases(refs: Sequence[Path]) -> tuple[RiskCase, ...]:
    """Read ordered case arrays without reconstructing later account facts.

    Example:
        cases = read_risk_cases((Path("/data/cases.json"),))
    """
    cases = tuple(case for ref in refs
                  for case in TypeAdapter(tuple[RiskCase, ...]).validate_json(ref.read_bytes(), strict=True))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("risk case_id must be unique")
    return cases


def evaluate_risk_cases(cases: Sequence[RiskCase], *, risk: RiskManager) -> tuple[RiskObservation, ...]:
    """Run the same risk calculation used in a trading feedback loop.

    Example:
        observations = evaluate_risk_cases(cases, risk=risk_manager)
    """
    return tuple(RiskObservation(case, risk.apply(target=case.target, ctx=case.context)) for case in cases)
