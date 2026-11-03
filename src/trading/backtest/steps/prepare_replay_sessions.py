# filepath: src/trading/backtest/steps/prepare_replay_sessions.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from src.access import Access
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.market.replay import ReplaySession
from src.trading.core.time import TradingWindow
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class PrepareReplaySessionsStep:
    """Prepare regular stock account days from the formal calendar.

    Example:
        context = PrepareReplaySessionsStep(access, "2026-07-17", "2026-07-21").run(context)
    """

    access: Access
    start_date: str
    end_date: str

    def run(self, context: BacktestContext) -> BacktestContext:
        """Read open dates and declare account days independently of available bars.

        Example:
            context = step.run(context)
        """
        days = self.access.trade_dates(start_date=self.start_date, end_date=self.end_date)
        if not days:
            raise ValueError("replay input range must contain at least one formal trading day")
        sessions: list[ReplaySession] = []
        for day in days:
            start, morning_open, morning_close, afternoon_open, close = (
                DateTimeUtils.local_time_to_utc_epoch_us(clock_time, date.fromisoformat(day))
                for clock_time in (time(0), time(9, 30), time(11, 30), time(13), time(15))
            )
            sessions.append(ReplaySession(
                trade_date=day, start_ts_us=start, close_ts_us=close,
                windows=(TradingWindow(day, morning_open, morning_close), TradingWindow(day, afternoon_open, close)),
            ))
        context.sessions = tuple(sessions)
        return context
