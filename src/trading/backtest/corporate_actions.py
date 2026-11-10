# filepath: src/trading/backtest/corporate_actions.py
"""Prepared corporate-action schedules and explicitly unresolved source facts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.trading.portfolio.corporate_actions import Distribution
from src.data_system.corporate_actions import CorporateActionResolution
from src.trading.backtest.market.replay import ReplaySession


class CorporateActionNotice(BaseModel):
    """A sourced non-distribution event requiring separate implementation or an election.

    Example:
        notice = CorporateActionNotice(action_id="000016/cash-option", symbol="000016",
            available_ts_us=1, effective_ts_us=2, kind="cash_option", source_ref="https://example.com/notice")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    available_ts_us: StrictInt
    effective_ts_us: StrictInt
    kind: Literal["cash_option", "merger", "delisting", "other"]
    source_ref: str = Field(min_length=1)


class CorporateActionIssue(BaseModel):
    """An unresolved event or missing coverage, distinct from absent trading prices.

    Example:
        issue = CorporateActionIssue(ts_us=2, symbol="600000", action_id="dividend/600000",
            reason="fractional_shares", source_ref="dividend/v1/2026-09-02")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: StrictInt
    symbol: str | None = None
    action_id: str | None = None
    reason: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


@dataclass(slots=True)
class CorporateActionSchedule:
    """Index already validated events once; replay Steps consume their own phase.

    Example:
        schedule = CorporateActionSchedule(distributions=(action,), notices=())
        registrations = schedule.registrations.get(close_us, ())
    """

    distributions: tuple[Distribution, ...] = ()
    notices: tuple[CorporateActionNotice, ...] = ()
    issues: tuple[CorporateActionIssue, ...] = ()
    revisions: tuple[Distribution, ...] = ()
    resolutions: tuple[CorporateActionResolution, ...] = ()
    registrations: dict[int, list[Distribution]] = field(default_factory=dict, init=False)
    recognitions: dict[int, list[Distribution]] = field(default_factory=dict, init=False)
    payments: dict[int, list[Distribution]] = field(default_factory=dict, init=False)
    releases: dict[int, list[Distribution]] = field(default_factory=dict, init=False)
    versions: tuple[Distribution, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        self.versions = tuple(sorted((*self.distributions, *self.revisions), key=lambda action: action.announced_ts_us))
        initial = {action.action_id: action for action in self.distributions}
        if len(initial) != len(self.distributions):
            raise ValueError("distributions require distinct event identities")
        previous = dict(initial)
        for action in sorted(self.revisions, key=lambda item: item.announced_ts_us):
            prior = previous.get(action.action_id)
            if (prior is None or action.kind != prior.kind or action.announced_ts_us <= prior.announced_ts_us
                or (action.symbol, action.record_ts_us) != (prior.symbol, prior.record_ts_us)):
                raise ValueError("distribution revisions require stable identity and increasing availability")
            if any(old is not None and new is None for old, new in (
                (prior.cash_per_share, action.cash_per_share), (prior.shares_per_share, action.shares_per_share),
            )):
                raise ValueError("known distribution amounts cannot become unknown")
            previous[action.action_id] = action
        for action in self.versions:
            phases = (
                (self.registrations, action.record_ts_us),
                (self.recognitions, action.ex_ts_us),
                (self.payments, action.pay_ts_us),
                (self.releases, action.shares_available_ts_us),
            )
            for index, stamp in phases:
                if index is self.registrations and action.cash_per_share == 0 and action.shares_per_share == 0:
                    continue
                if stamp is not None and action.announced_ts_us <= stamp:
                    bucket = index.setdefault(stamp, [])
                    if not any(item.action_id == action.action_id for item in bucket):
                        bucket.append(action)

    def timestamps(self, sessions: tuple[ReplaySession, ...]) -> tuple[int, ...]:
        """Include events even when there is neither market data nor a new order.

        Example:
            times = schedule.timestamps(sessions)
        """
        notice_times: set[int] = set()
        for notice in self.notices:
            visible = max(notice.available_ts_us, notice.effective_ts_us)
            for session in sessions:
                if visible <= session.close_ts_us:
                    notice_times.add(max(visible, session.start_ts_us))
                    break
        for action in (*self.distributions, *self.revisions):
            if action.kind == "compensation":
                for stamp in (action.record_ts_us + 1, action.pay_ts_us,
                    action.shares_credit_ts_us, action.shares_available_ts_us):
                    if stamp is not None and stamp >= action.announced_ts_us:
                        for session in sessions:
                            if stamp <= session.close_ts_us:
                                notice_times.add(max(stamp, session.start_ts_us))
                                break
            for session in sessions:
                if action.announced_ts_us <= session.close_ts_us:
                    notice_times.add(max(action.announced_ts_us, session.start_ts_us))
                    break
        return tuple(sorted(set(self.registrations) | set(self.recognitions) | set(self.payments)
            | set(self.releases) | notice_times))
