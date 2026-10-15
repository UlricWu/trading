# filepath: src/trading/backtest/steps/prepare_initial_marks.py
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import cast

from src.access import Access
from src.config.backtest_config import InitialAccount
from src.trading.backtest.context import BacktestContext, BacktestInputs
from src.trading.backtest.market.replay import MarkObservation, ReplayMarket
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class PrepareInitialMarksStep:
    """Load prior raw closes only for initial holdings without a visible replay price.

    Example:
        context = PrepareInitialMarksStep(access, initial_account).run(context)
    """

    access: Access
    initial_account: InitialAccount

    def run(self, context: BacktestContext) -> BacktestContext:
        """Search backward over formal sessions; never read future prices to seed holdings.

        Example:
            context = step.run(context)
        """
        missing = {symbol for symbol, qty in self.initial_account.positions.items() if qty}
        if not missing:
            return context
        inputs = cast(BacktestInputs, context.inputs)
        visible = ReplayMarket(inputs.market)
        visible.on_time(self.initial_account.ts_us)
        missing.difference_update(visible.prices)
        marks: list[MarkObservation] = []
        day = self.initial_account.trade_date
        while missing:
            day = self.access.recent_trade_dates(end_date=day, sessions=2)[0]
            bars = self.access.daily_bars(trade_date=day)
            selected = bars.loc[bars["symbol"].isin(missing)]
            if not selected.empty:
                prices = {symbol: Decimal(str(close)) for symbol, close in
                          selected.loc[:, ["symbol", "close"]].itertuples(index=False, name=None)}
                marks.append(MarkObservation(
                    ts_us=DateTimeUtils.local_time_to_utc_epoch_us(time(15), date.fromisoformat(day)),
                    trade_date=day, prices=prices, kind="daily_close",
                ))
                missing.difference_update(prices)
        context.initial_marks = tuple(reversed(marks))
        return context
