# filepath: src/trading/backtest/commands.py
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Self

from pydantic import TypeAdapter

from src.trading.backtest.market.replay import ReplayMarket
from src.trading.core.events import OrderIntent, CancelOrders


@dataclass(frozen=True, slots=True)
class OrderCommandSchedule:
    """Validate fixed commands for one replay and keep their original order.

    Construction checks chronology, request dates and the simulation interval.
    The immutable index retains neither the caller's container nor the market
    and range inputs. Queries never consume or execute commands.

    Example:
        commands = OrderCommandSchedule((CancelOrders(ts_us=10, order_ids=(1,)),),
            start="2026-07-03", end="2026-07-03", market=market,
            start_ts_us=0, end_ts_us=100)
        at_tick = commands.at(10)
    """

    commands: InitVar[Sequence[OrderIntent | CancelOrders]]
    start: InitVar[str]
    end: InitVar[str]
    market: InitVar[ReplayMarket]
    start_ts_us: InitVar[int]
    end_ts_us: InitVar[int]
    _by_time: Mapping[int, tuple[OrderIntent | CancelOrders, ...]] = field(init=False, repr=False)

    def __post_init__(
        self, commands: Sequence[OrderIntent | CancelOrders], start: str, end: str,
        market: ReplayMarket, start_ts_us: int, end_ts_us: int,
    ) -> None:
        if any(a.ts_us > b.ts_us for a, b in zip(commands, commands[1:])):
            raise ValueError("frozen order/cancel commands must retain chronological order")
        by_time: dict[int, list[OrderIntent | CancelOrders]] = defaultdict(list)
        for command in commands:
            if not start <= market.session_at(command.ts_us).trade_date <= end:
                raise ValueError("frozen command date is outside the selected request range")
            if not start_ts_us <= command.ts_us <= end_ts_us:
                raise ValueError("frozen command is outside the simulation interval")
            by_time[command.ts_us].append(command)
        object.__setattr__(self, '_by_time', MappingProxyType({ts: tuple(items) for ts, items in by_time.items()}))

    @classmethod
    def from_files(
        cls, order_refs: Sequence[Path], *, start: str, end: str, market: ReplayMarket,
        start_ts_us: int, end_ts_us: int,
    ) -> Self:
        """Read frozen commands in file order and construct a validated schedule.

        Strict deserialization owns file structure; construction checks chronology
        and replay coverage. File and validation failures propagate to the caller.

        Example:
            commands = OrderCommandSchedule.from_files((Path("/data/orders.json"),),
                start="2026-07-03", end="2026-07-03", market=market,
                start_ts_us=initial_account.ts_us, end_ts_us=execution_config.end_ts_us)
            times = commands.timestamps()
        """
        commands = tuple(command for ref in order_refs
            for command in TypeAdapter(tuple[OrderIntent | CancelOrders, ...]).validate_json(ref.read_bytes(), strict=True))
        return cls(commands, start=start, end=end, market=market, start_ts_us=start_ts_us, end_ts_us=end_ts_us)

    def timestamps(self) -> tuple[int, ...]:
        """Return unique command times in their validated chronological order.

        Example:
            times = commands.timestamps()
        """
        return tuple(self._by_time)

    def at(self, ts_us: int) -> tuple[OrderIntent | CancelOrders, ...]:
        """Return the original same-time sequence, or an empty tuple at other times.

        Example:
            at_tick = commands.at(10)
        """
        return self._by_time.get(ts_us, ())
