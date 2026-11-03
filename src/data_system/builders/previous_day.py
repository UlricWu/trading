# filepath: src/data_system/builders/previous_day.py
"""Build preopen samples using previous-session inputs and next-day high labels."""

from collections.abc import Sequence
from datetime import date, time
import re
from types import MappingProxyType

import numpy as np
import pandas as pd
import pyarrow as pa

from src.data_system.builders.stock_1430 import STOCK_1430_FEATURE_SCHEMA, _label_window_vwap, _positive_ratio
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils
from src.utils.price_utils import apply_asof_price_adjustment

PREOPEN_DECISION_TIME = time(9)
PREOPEN_AVAILABLE_TIME = time(22)
HIGH_EVENT_THRESHOLD = 0.9
DAILY_SOURCE_COLUMNS = (
    "f_d_close_return_1d", "f_d_open_gap_1d", "f_d_log_amount",
    "f_d_max_drawdown_20d_asof_tminus1", "f_d_close_volatility_60d_asof_tminus1",
    "f_d_close_return_5d_asof_tminus1", "f_d_turnover_rate_mean_20d_asof_tminus1",
)
PREOPEN_FEATURE_COLUMNS = tuple(f"lag_{name}" for name in STOCK_1430_FEATURE_SCHEMA.names[3:]) + tuple(
    f"lag_{name}_rank" for name in DAILY_SOURCE_COLUMNS
)
PREOPEN_RULES = MappingProxyType({
    "rule_amount": "baseline_amount", "rule_momentum": "baseline_momentum",
    "rule_reversal": "baseline_reversal", "rule_volatility": "lag_f_d_close_volatility_60d_asof_tminus1_rank",
    "rule_turnover": "lag_f_d_turnover_rate_mean_20d_asof_tminus1_rank",
})


def qualify_previous_day(
    symbols: Sequence[str], *, listed: pd.DataFrame, st: pd.DataFrame,
    suspension: pd.DataFrame, target_date: str,
) -> pd.DataFrame:
    """Resolve prior-session membership and suspension without future labels.

    Example:
        eligibility = qualify_previous_day(symbols, listed=listed, st=st, suspension=events, target_date="2026-07-01")
    """
    table_ops.require_unique(listed, ("symbol",), who="previous-day listing")
    table_ops.require_columns(listed, ("symbol", "list_date"), who="previous-day listing")
    table_ops.require_columns(st, ("symbol",), who="previous-day ST")
    table_ops.require_columns(suspension, ("symbol", "suspend_type", "suspend_timing"), who="previous-day suspension")
    if not suspension.suspend_type.isin(("S", "R")).all():
        raise ValueError("previous-day suspension has an unknown suspend_type")
    states: dict[str, str] = {}
    for symbol, events in suspension.groupby("symbol", sort=True):
        resumes = events.suspend_type.eq("R").any()
        state = "resumed" if resumes else "not_halted"
        for timing in events.loc[events.suspend_type.eq("S"), "suspend_timing"]:
            if pd.isna(timing):
                state = "unknown" if resumes else "halted"
                break
            ends = []
            for interval in str(timing).split(","):
                match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", interval)
                if match is None:
                    state = "unknown"
                    break
                h1, m1, h2, m2 = map(int, match.groups())
                start, end = h1 * 60 + m1, h2 * 60 + m2
                if not (0 <= m1 < 60 and 0 <= m2 < 60 and 570 <= start < end <= 900):
                    state = "unknown"
                    break
                ends.append(end)
            if state == "unknown":
                break
            if max(ends) == 900:
                state = "unknown" if resumes else "halted"
                break
        states[str(symbol)] = state
    qualified = pd.DataFrame({"symbol": symbols}).merge(
        listed[["symbol", "list_date"]], on="symbol", how="left", validate="one_to_one", indicator=True,
    )
    listed_dates = pd.to_datetime(qualified.list_date, format="%Y-%m-%d", errors="coerce")
    qualified["suspension_state"] = qualified.symbol.map(states)
    qualified["reason"] = np.select(
        [qualified._merge.eq("left_only"), listed_dates.isna(), listed_dates.gt(pd.Timestamp(target_date)),
         qualified.symbol.isin(st.symbol), qualified.suspension_state.eq("halted").fillna(False),
         qualified.suspension_state.eq("unknown").fillna(False)],
        ["source_uncovered", "listing_date_unknown", "not_yet_listed", "prior_st", "prior_suspended", "suspension_unknown"],
        default="eligible",
    )
    qualified["eligible"] = qualified.reason.eq("eligible")
    return qualified.drop(columns="_merge")


def build_previous_day_features(
    *, l2: pd.DataFrame, daily: pd.DataFrame, trade_date: str, previous_trade_date: str,
) -> pd.DataFrame:
    """Rank daily inputs on the original L2-P universe before qualification.

    Example:
        features = build_previous_day_features(l2=l2_p, daily=daily_p, trade_date="2026-07-01", previous_trade_date="2026-06-30")
    """
    for frame in (l2, daily):
        table_ops.require_unique(frame, ("symbol",), who="previous-day features")
        if not frame.trade_date.eq(previous_trade_date).all():
            raise ValueError("previous-day feature input date mismatch")
    if previous_trade_date >= trade_date:
        raise ValueError("previous-day inputs must precede the decision date")
    aligned = daily.set_index("symbol").reindex(l2.symbol)
    columns = STOCK_1430_FEATURE_SCHEMA.names[3:]
    features = l2[["symbol", *columns]].rename(columns={name: f"lag_{name}" for name in columns})
    ranks = aligned[list(DAILY_SOURCE_COLUMNS)].rank(method="average", pct=True)
    for column in DAILY_SOURCE_COLUMNS:
        features[f"lag_{column}_rank"] = ranks[column].to_numpy()
    features["trade_date"] = trade_date
    features["decision_ts_utc"] = DateTimeUtils.local_time_to_utc_epoch_us(PREOPEN_DECISION_TIME, date.fromisoformat(trade_date))
    features["source_date"] = previous_trade_date
    features["feature_ready_ts"] = DateTimeUtils.local_time_to_utc_epoch_us(PREOPEN_AVAILABLE_TIME, date.fromisoformat(previous_trade_date))
    features["baseline_amount"] = aligned.f_d_log_amount.to_numpy()
    features["baseline_momentum"] = aligned.f_d_close_return_5d_asof_tminus1.to_numpy()
    features["baseline_reversal"] = -features.baseline_momentum
    if np.isinf(features[list(PREOPEN_FEATURE_COLUMNS)].to_numpy(dtype=float)).any():
        raise ValueError("previous-day features contain infinity")
    return features


def build_next_day_high_labels(
    *, symbols: Sequence[str], entry_minutes: pa.Table, exit_minutes: pa.Table,
    entry_factors: pd.DataFrame, exit_factors: pd.DataFrame, trade_date: str, next_trade_date: str,
) -> pd.DataFrame:
    """Rank adjusted entry-window to next-day all-session high on the T universe.

    Example:
        labels = build_next_day_high_labels(symbols=symbols_t, entry_minutes=minutes_t,
            exit_minutes=minutes_t1, entry_factors=factors_t, exit_factors=factors_t1,
            trade_date="2026-07-01", next_trade_date="2026-07-02")
    """
    entry = _label_window_vwap(entry_minutes, trade_date=date.fromisoformat(trade_date))
    high = exit_minutes.select(["symbol", "high"]).group_by("symbol", use_threads=False).aggregate(
        [("high", "max")]
    ).to_pandas().set_index("symbol").high_max
    adjusted = []
    for prices, factors, day in ((entry, entry_factors, trade_date), (high, exit_factors, next_trade_date)):
        frame = pd.DataFrame({"raw_price": prices})
        frame["adj_factor"] = factors.set_index("symbol").adj_factor.reindex(frame.index)
        adjusted.append(apply_asof_price_adjustment(
            frame, adjustment="hfq", asof_date=day, price_columns=("raw_price",), output_prefix="adjusted_",
        ).adjusted_raw_price)
    labels = pd.DataFrame({"symbol": symbols})
    returns = _positive_ratio(labels.symbol.map(adjusted[1]), labels.symbol.map(adjusted[0])) - 1
    labels["y_rank_return"] = returns.rank(method="average", pct=True)
    labels["y_event"] = labels.y_rank_return.gt(HIGH_EVENT_THRESHOLD).astype(float).where(labels.y_rank_return.notna())
    return labels
