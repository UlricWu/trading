# filepath: src/data_system/builders/label_daily_close_return_rank.py
"""Single-maturity forward adjusted-close return-rank labels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

import pandas as pd
import pyarrow as pa

from src.access import Access
from src.utils import table_ops
from src.utils.price_utils import apply_asof_price_adjustment

_KEY_COLUMNS = ("symbol", "trade_date")


@dataclass(frozen=True, slots=True)
class DailyCloseReturnRankV1Builder:
    """Build one adjusted-close return rank at one fixed maturity.

    Example:
        labels = DailyCloseReturnRankV1Builder(lookahead=3).build(
            access=access,
            trade_dates=(
                "2026-07-15",
                "2026-07-16",
                "2026-07-17",
                "2026-07-20",
            ),
        )
    """

    lookahead: int
    label_column: ClassVar[str] = "y_rank_return"

    def __post_init__(self) -> None:
        if not isinstance(self.lookahead, int) or isinstance(self.lookahead, bool):
            raise TypeError("lookahead must be an int")
        if self.lookahead <= 0:
            raise ValueError("lookahead must be positive")

    def build(
        self,
        *,
        access: Access,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        """Return labels for the first date of the exact maturity window.

        Example:
            labels = DailyCloseReturnRankV1Builder(lookahead=1).build(
                access=access,
                trade_dates=("2026-07-17", "2026-07-20"),
            )
        """
        if isinstance(trade_dates, str):
            raise TypeError("trade_dates must be a sequence of dates")
        dates = tuple(trade_dates)
        required_dates = self.lookahead + 1
        if len(dates) != required_dates:
            raise ValueError(
                f"daily close-return rank d{self.lookahead} requires "
                f"{required_dates} trade dates"
            )

        signal_date = dates[0]
        maturity_date = dates[-1]
        signal = _adjusted_close_rows(access=access, trade_date=signal_date)
        maturity = _adjusted_close_rows(
            access=access,
            trade_date=maturity_date,
        ).rename(columns={"adjusted_close": "maturity_adjusted_close"})
        frame = signal.merge(
            maturity.loc[:, ["symbol", "maturity_adjusted_close"]],
            on="symbol",
            how="left",
            validate="one_to_one",
        )

        returns = frame["maturity_adjusted_close"] / frame["adjusted_close"] - 1.0
        frame[self.label_column] = returns.rank(
            method="average",
            ascending=True,
            pct=True,
        ).astype("Float64")
        output = frame.loc[:, [*_KEY_COLUMNS, self.label_column]].sort_values(
            list(_KEY_COLUMNS)
        )
        return pa.Table.from_pandas(output, preserve_index=False)


def _adjusted_close_rows(*, access: Access, trade_date: str) -> pd.DataFrame:
    bars = access.daily_bars(trade_date=trade_date)
    table_ops.require_columns(
        bars,
        ("symbol", "trade_date", "close"),
        who="daily_close_return_rank daily_bar",
    )
    factors = access.adjustment_factors(trade_date=trade_date)
    frame = bars.loc[:, ["symbol", "trade_date", "close"]].merge(
        factors,
        on=list(_KEY_COLUMNS),
        how="left",
        validate="one_to_one",
    )
    return apply_asof_price_adjustment(
        frame,
        adjustment="hfq",
        asof_date=trade_date,
        price_columns=("close",),
        output_prefix="adjusted_",
    ).loc[:, ["symbol", "trade_date", "adjusted_close"]]
