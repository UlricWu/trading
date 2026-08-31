# filepath: src/trading/reporting/forward_return.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Symbol = str


@dataclass(slots=True)
class PendingVec:
    ts0_us: int
    ts1_us: int
    symbols: np.ndarray  # dtype=object (Symbol)
    price0: np.ndarray   # dtype=float64


class ForwardReturnEngine:
    """
    Vectorized strict forward-return engine.

    Contract:
    - register(ts0): take (symbols, price0_vec) at ts0, create pending label for ts1=ts0+horizon
    - settle(ts): for all matured pendings (ts1<=ts), use current (symbols, price1_vec) to compute
      forward returns. Invalid p0/p1 (nan/inf/<=0) are dropped.

    Output:
    - list of (ts0, symbols_vec, fwd_ret_vec) for each matured ts0
    """

    def __init__(self, *, horizon_minutes: int) -> None:
        if isinstance(horizon_minutes, bool) or not isinstance(horizon_minutes, int):
            raise TypeError("horizon_minutes must be an int")
        if horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        self.horizon_minutes = horizon_minutes
        self._pending: dict[int, PendingVec] = {}

    @property
    def horizon_us(self) -> int:
        return self.horizon_minutes * 60 * 1_000_000

    def register(
        self,
        *,
        ts_us: int,
        symbols: Sequence[Symbol],
        price0: np.ndarray,
    ) -> None:
        """
        Register pending labels at ts0 using price0 at ts0.
        Idempotent on ts0: if already registered, ignore.
        """
        ts0 = int(ts_us)
        if ts0 in self._pending:
            return

        sym_arr = np.asarray(list(symbols), dtype=object)
        p0 = np.asarray(price0, dtype=np.float64)

        if sym_arr.shape[0] != p0.shape[0]:
            raise ValueError("symbols and price0 must have same length")

        # strict valid p0
        m0 = np.isfinite(p0) & (p0 > 0.0)
        if not np.any(m0):
            return

        self._pending[ts0] = PendingVec(
            ts0_us=ts0,
            ts1_us=ts0 + self.horizon_us,
            symbols=sym_arr[m0],
            price0=p0[m0],
        )

    def settle(
        self,
        *,
        ts_us: int,
        symbols: Sequence[Symbol],
        price1: np.ndarray,
    ) -> list[tuple[int, np.ndarray, np.ndarray]]:
        """
        Settle all pending labels whose ts1 <= current ts.

        We accept current (symbols, price1_vec) and do vectorized lookup by building a map
        sym->index for *current bar* once, then index into it.

        Returns: list[(ts0, sym_vec, ret_vec)]
        """
        ts = int(ts_us)
        if not self._pending:
            return []

        sym_now = np.asarray(list(symbols), dtype=object)
        p1_now = np.asarray(price1, dtype=np.float64)
        if sym_now.shape[0] != p1_now.shape[0]:
            raise ValueError("symbols and price1 must have same length")

        # Build lookup: symbol -> current index
        # N~4700: dict lookup is OK; overall still vectorized in numeric ops.
        idx_map: dict[Symbol, int] = {str(s): i for i, s in enumerate(sym_now)}

        matured_ts0 = [ts0 for ts0, pend in self._pending.items() if pend.ts1_us <= ts]
        if not matured_ts0:
            return []

        out: list[tuple[int, np.ndarray, np.ndarray]] = []
        for ts0 in matured_ts0:
            pend = self._pending.pop(ts0)

            # gather p1 for pend.symbols
            idx = np.fromiter(
                (idx_map.get(str(s), -1) for s in pend.symbols),
                dtype=np.int64,
                count=pend.symbols.shape[0],
            )

            ok = idx >= 0
            if not np.any(ok):
                continue

            p0 = pend.price0[ok]
            p1 = p1_now[idx[ok]]

            # strict valid p1
            m1 = np.isfinite(p1) & (p1 > 0.0)
            if not np.any(m1):
                continue

            sym = pend.symbols[ok][m1]
            p0 = p0[m1]
            p1 = p1[m1]

            ret = p1 / p0 - 1.0
            # guard numeric
            mret = np.isfinite(ret)
            if not np.any(mret):
                continue

            out.append((pend.ts0_us, sym[mret], ret[mret]))

        return out
