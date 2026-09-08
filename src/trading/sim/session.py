# filepath: src/trading/sim/session.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.core.time import ReplayClock
from src.trading.market.data_view import MarketDataView
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class ReplaySession:
    """A single replay session bound to one data view and its real bar clock."""

    trade_date: str
    data_view: MarketDataView
    clock: ReplayClock

    @classmethod
    def from_data_view(
        cls,
        data_view: MarketDataView,
        *,
        start_us: int | None = None,
        end_us: int | None = None,
    ) -> "ReplaySession":
        clock = ReplayClock.from_data_view(
            data_view,
            start_us=start_us,
            end_us=end_us,
        )
        if len(clock) == 0:
            raise RuntimeError("ReplaySession requires a non-empty clock")

        try:
            trade_date = data_view.trade_date
        except AttributeError as exc:
            raise ValueError("ReplaySession requires data_view.trade_date") from exc
        if trade_date is None:
            raise ValueError("ReplaySession requires data_view.trade_date")
        trade_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )

        return cls(
            trade_date=trade_date,
            data_view=data_view,
            clock=clock,
        )

    @property
    def frequency(self) -> str:
        return str(self.data_view.frequency)
