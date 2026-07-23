# filepath: src/trading/market/minute_feature_view.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

import numpy as np
import pyarrow as pa

from src.trading.market.data_view import MarketDataView
from src.pipeline.phase import TRADING
from src.utils.datetime_utils import DateTimeUtils

AdjustmentType = Literal["raw", "adjusted"]


class TableResolver(Protocol):
    def get_many(self, symbols: Sequence[str]) -> Mapping[str, pa.Table]: ...


class MinuteFeatureDataView(MarketDataView):
    """
    MinuteFeatureDataView (TIME-MAJOR / UNION AXIS VERSION)

    Architecture:
        - Global union time axis
        - feature_cube: (n_bars, n_symbols, n_features)
        - price_mat:   (n_bars, n_symbols)
        - phase_mat:   (n_bars, n_symbols)

    Runtime:
        - on_time() updates single bar_idx
        - get_feature_matrix() is O(1) slice
        - No per-symbol pointer
    """

    # ============================================================
    # INIT
    # ============================================================

    def __init__(
        self,
        *,
        resolver: TableResolver,
        adj_resolver: TableResolver | None,
        symbols: Sequence[str],
        feature_names: Sequence[str],
        ts_col: str = "ts_utc",
        price_col: str = "close",
        adjustment: AdjustmentType = "raw",
    ) -> None:
        if not symbols:
            raise RuntimeError("MinuteFeatureDataView requires explicit symbols")
        if len(symbols) != len(set(symbols)):
            raise ValueError("symbols must be unique")

        if not feature_names:
            raise RuntimeError("feature_names empty")
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("feature_names must be unique")
        if adjustment not in {"raw", "adjusted"}:
            raise ValueError(f"unsupported adjustment: {adjustment}")

        self._resolver = resolver
        self._adj_resolver = adj_resolver

        self._symbols: list[str] = list(symbols)
        self._sym2idx: dict[str, int] = {
            s: i for i, s in enumerate(self._symbols)
        }

        self._feature_names: list[str] = list(feature_names)
        self._ts_col = ts_col
        self._price_col = price_col
        self._adjustment = adjustment

        # ----------------------------------------------------------
        # Load all tables
        # ----------------------------------------------------------
        tables = self._resolver.get_many(self._symbols)

        # ----------------------------------------------------------
        # Build UNION time axis
        # ----------------------------------------------------------
        ts_list = []
        for s in self._symbols:
            tbl = tables[s].combine_chunks()
            ts_arr = tbl[self._ts_col].to_numpy()
            if ts_arr.size > 0:
                ts_list.append(ts_arr)

        if not ts_list:
            raise RuntimeError("No minute data found")

        global_ts = np.unique(np.concatenate(ts_list))
        global_ts.sort()

        self._bar_ts: np.ndarray = global_ts.astype(np.int64)
        self._bar_idx: int = 0

        n_bars = int(self._bar_ts.shape[0])
        n_syms = int(len(self._symbols))
        n_feats = int(len(self._feature_names))

        # ----------------------------------------------------------
        # Allocate matrices (NaN-safe)
        # ----------------------------------------------------------
        self._feature_cube = np.full(
            (n_bars, n_syms, n_feats),
            np.nan,
            dtype=np.float64,
        )

        self._price_mat = np.full(
            (n_bars, n_syms),
            np.nan,
            dtype=np.float64,
        )

        self._phase_mat = np.zeros(
            (n_bars, n_syms),
            dtype=np.int32,
        )

        td_vec = np.full(n_bars, "", dtype=object)

        # ----------------------------------------------------------
        # Fill matrices
        # ----------------------------------------------------------
        for j, s in enumerate(self._symbols):
            tbl = tables[s].combine_chunks()

            ts_local = tbl[self._ts_col].to_numpy()
            if ts_local.size == 0:
                continue

            idx = np.searchsorted(self._bar_ts, ts_local)
            td_local = tbl["trade_date"].to_pylist()
            for row_idx, global_idx in enumerate(idx):
                trade_date = DateTimeUtils.normalize_source_date(
                    td_local[row_idx],
                    field_name="trade_date",
                )
                current = td_vec[int(global_idx)]
                if current and current != trade_date:
                    raise RuntimeError(
                        "[MinuteFeatureDataView] conflicting trade_date for "
                        f"ts_us={int(ts_local[row_idx])}: {current} vs {trade_date}"
                    )
                td_vec[int(global_idx)] = trade_date

            # price
            self._price_mat[idx, j] = tbl[self._price_col].to_numpy()

            # phase
            self._phase_mat[idx, j] = tbl["phase"].to_numpy()

            # features
            cols = [
                tbl[f].to_numpy()
                for f in self._feature_names
            ]
            feats = np.column_stack(cols)

            self._feature_cube[idx, j, :] = feats

        # ----------------------------------------------------------
        # Adjustment lookup (optional)
        # ----------------------------------------------------------
        self._adj_lookup: dict[str, dict[str, float]] | None = None
        if adjustment == "adjusted" and adj_resolver is not None:
            self._adj_lookup = {}
            adj_tables = adj_resolver.get_many(self._symbols)
            for s, t in adj_tables.items():
                tt = t.combine_chunks()
                td = tt["trade_date"].to_pylist()
                af = tt["adj_factor"].to_numpy()
                m: dict[str, float] = {}
                for k in range(len(td)):
                    if td[k] is not None and af[k] is not None:
                        m[
                            DateTimeUtils.normalize_source_date(
                                td[k],
                                field_name="trade_date",
                            )
                        ] = float(af[k])
                self._adj_lookup[s] = m

        missing_trade_dates = np.flatnonzero(td_vec == "")
        if missing_trade_dates.size:
            raise RuntimeError("[MinuteFeatureDataView] missing trade_date for bar")

        self._trade_date_vec = td_vec

        self._current_ts: int | None = None
        self._last_trading_ts_us: int | None = None

        self._min_ts_us = int(self._bar_ts[0])
        self._max_ts_us = int(self._bar_ts[-1])

    # ============================================================
    # TIME
    # ============================================================

    def on_time(self, ts_us: int) -> None:
        ts_us = int(ts_us)
        if ts_us < self._min_ts_us:
            raise RuntimeError(
                f"timestamp precedes first observable bar: {ts_us} < {self._min_ts_us}"
            )

        i = int(np.searchsorted(self._bar_ts, ts_us, side="right") - 1)
        if i >= self._bar_ts.shape[0]:
            i = self._bar_ts.shape[0] - 1

        self._bar_idx = i
        self._current_ts = ts_us
        phase = self.get_phase(self._symbols[0])
        if phase == TRADING:
            self._last_trading_ts_us = ts_us

    # ============================================================
    # MATRIX ACCESS (FAST PATH)
    # ============================================================

    def get_feature_matrix(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        if symbols == self._symbols:
            return self._feature_cube[self._bar_idx]

        idx = np.fromiter(
            (self._sym2idx[s] for s in symbols),
            dtype=np.int64,
        )
        return self._feature_cube[self._bar_idx, idx, :]

    def get_price_vector(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        if symbols == self._symbols:
            return self._price_mat[self._bar_idx]

        idx = np.fromiter(
            (self._sym2idx[s] for s in symbols),
            dtype=np.int64,
        )
        return self._price_mat[self._bar_idx, idx]

    def get_price(self, symbol: str) -> float | None:
        self._require_active()
        j = self._sym2idx[symbol]
        px = self._price_mat[self._bar_idx, j]

        if not np.isfinite(px):
            return None

        if self._adjustment == "raw" or self._adj_lookup is None:
            return float(px)

        td = str(self._trade_date_vec[self._bar_idx])
        adj = self._adj_lookup.get(symbol, {}).get(td)

        return float(px) if adj is None else float(px) * float(adj)

    def get_phase(self, symbol: str) -> int | None:
        self._require_active()
        j = self._sym2idx[symbol]
        return int(self._phase_mat[self._bar_idx, j])

    # ============================================================
    # META
    # ============================================================

    def bar_timestamps_us(self) -> list[int]:
        return self._bar_ts.tolist()

    def time_bounds_us(self) -> tuple[int, int]:
        return self._min_ts_us, self._max_ts_us

    def last_trading_ts(self) -> int:
        if self._last_trading_ts_us is None:
            raise RuntimeError("no trading observed")
        return self._last_trading_ts_us

    @property
    def trade_date(self) -> str:
        self._require_active()
        return str(self._trade_date_vec[self._bar_idx])

    @property
    def frequency(self) -> str:
        return "minute"

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def _require_active(self) -> None:
        if self._current_ts is None:
            raise RuntimeError("on_time(ts_us) must be called before querying facts")
