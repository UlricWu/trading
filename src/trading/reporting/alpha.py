# filepath: src/trading/reporting/alpha.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

from src.trading.reporting.alpha_tape import AlphaPoint, AlphaTape
from src.trading.reporting.forward_return import ForwardReturnEngine

Symbol = str


@dataclass(frozen=True, slots=True)
class AlphaEvalConfig:
    horizon_minutes: int = 5
    quantiles: int = 5
    min_cross_section: int = 50          # 建议：避免太小N导致相关系数不稳定
    score_clip: float | None = None
    strict_settle: bool = True           # True: p0/p1无效就丢样本（引擎已严格实现）


class AlphaEvaluator:
    """
    Fully-vectorized alpha evaluator.

    Pipeline:
    - on_scores(ts, symbols, scores_vec, price_vec):
        1) sanitize score vector
        2) register forward labels at ts0 with price0
        3) settle matured labels at current ts using current price1
        4) for each matured ts0, compute cross-sectional stats and append AlphaPoint
    """

    def __init__(self, *, cfg: AlphaEvalConfig) -> None:
        self.cfg = cfg
        self.tape = AlphaTape(points=[])
        self._fwd = ForwardReturnEngine(horizon_minutes=cfg.horizon_minutes)

        # cache score vectors at ts0: ts0 -> (symbols_vec, scores_vec, n_total, meta)
        self._score_cache: dict[
            int,
            tuple[np.ndarray, np.ndarray, int, Mapping[str, object] | None],
        ] = {}

    # ------------------------------------------------------------
    # Public
    # ------------------------------------------------------------

    def on_scores(
        self,
        *,
        ts_us: int,
        symbols: Sequence[Symbol],
        scores: Mapping[Symbol, float],
        price_vec: np.ndarray,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        ts = int(ts_us)
        sym = np.asarray(list(symbols), dtype=object)
        px = np.asarray(price_vec, dtype=np.float64)

        if sym.size == 0:
            return
        if sym.size != px.size:
            raise ValueError("symbols and price_vec length mismatch")

        # scores dict -> vector aligned to sym
        sc = self._scores_to_vec(sym, scores)
        if sc is None:
            return

        n_total = int(sc.size)

        # cache scores at ts0
        self._score_cache[ts] = (sym, sc, n_total, meta)

        # register forward labels at ts0 using price0 (vectorized engine will filter)
        self._fwd.register(ts_us=ts, symbols=sym.tolist(), price0=px)

        # settle matured labels at current ts using current price1
        matured = self._fwd.settle(ts_us=ts, symbols=sym.tolist(), price1=px)
        if not matured:
            return

        for ts0, sym0, ret0 in matured:
            cached = self._score_cache.pop(ts0, None)
            if cached is None:
                continue
            sym_sc, sc0, n_total0, meta0 = cached

            # align by symbol intersection: build lookup for ts0 symbols -> index
            idx0 = {str(s): i for i, s in enumerate(sym_sc)}
            idx = np.fromiter((idx0.get(str(s), -1) for s in sym0), dtype=np.int64, count=sym0.size)
            ok = idx >= 0
            if not np.any(ok):
                continue

            x = sc0[idx[ok]]
            y = ret0[ok]

            self._evaluate_and_append(
                ts0_us=int(ts0),
                x=x,
                y=y,
                n_total=int(n_total0),
                meta=meta0,
            )

    def finalize(self) -> dict[str, float | int]:
        if not self.tape.points:
            return {
                "horizon_minutes": self.cfg.horizon_minutes,
                "count": 0,
                "ic_mean": np.nan,
                "ic_std": np.nan,
                "ic_ir": np.nan,
                "ic_tstat": np.nan,
                "ls_mean": np.nan,
                "ls_std": np.nan,
                "ls_ir": np.nan,
                "ls_tstat": np.nan,
            }

        ic = np.asarray([p.ic_spearman for p in self.tape.points], dtype=np.float64)
        ls = np.asarray([p.ls_ret for p in self.tape.points], dtype=np.float64)

        return {
            "horizon_minutes": int(self.cfg.horizon_minutes),
            "count": int(ic.size),
            "ic_mean": float(np.nanmean(ic)),
            "ic_std": float(np.nanstd(ic, ddof=1)) if ic.size > 1 else np.nan,
            "ic_ir": float(self._ir(ic)),
            "ic_tstat": float(self._tstat(ic)),
            "ls_mean": float(np.nanmean(ls)),
            "ls_std": float(np.nanstd(ls, ddof=1)) if ls.size > 1 else np.nan,
            "ls_ir": float(self._ir(ls)),
            "ls_tstat": float(self._tstat(ls)),
        }

    # ------------------------------------------------------------
    # Internal: vectorized + stable
    # ------------------------------------------------------------

    def _scores_to_vec(
        self,
        sym: np.ndarray,
        scores: Mapping[Symbol, float],
    ) -> np.ndarray | None:
        # build vector with NaN default
        sc = np.full((sym.size,), np.nan, dtype=np.float64)

        # fill
        # (dict lookup per symbol; 4700 OK; numeric ops stay vectorized)
        for i, s in enumerate(sym):
            v = scores.get(str(s))
            if v is None:
                continue
            fv = float(v)
            if not np.isfinite(fv):
                continue
            sc[i] = fv

        # sanitize
        m = np.isfinite(sc)
        if not np.any(m):
            return None

        if self.cfg.score_clip is not None:
            c = float(self.cfg.score_clip)
            sc = np.clip(sc, -c, c)

        # IMPORTANT: keep NaNs, later we mask with returns too
        return sc

    def _evaluate_and_append(
        self,
        *,
        ts0_us: int,
        x: np.ndarray,
        y: np.ndarray,
        n_total: int,
        meta: Mapping[str, object] | None,
    ) -> None:
        # mask finite pairs
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        m = np.isfinite(x) & np.isfinite(y)
        if not np.any(m):
            return

        x = x[m]
        y = y[m]
        n = int(x.size)
        if n < int(self.cfg.min_cross_section):
            return

        # Spearman: rank then Pearson on ranks (stable, avoids scipy spearman overhead per bar)
        rx = rankdata(x, method="average")
        ry = rankdata(y, method="average")
        ic_s = float(self._corr(rx, ry))
        ic_p = float(self._corr(x, y))

        ls_ret, spread = self._long_short_quantile(x=x, y=y, q=int(self.cfg.quantiles))

        # score dist
        score_min = float(np.min(x))
        score_p5 = float(np.quantile(x, 0.05))
        score_mean = float(np.mean(x))
        score_p95 = float(np.quantile(x, 0.95))
        score_max = float(np.max(x))

        coverage = float(n / max(1, int(n_total)))

        self.tape.append(
            AlphaPoint(
                ts_us=int(ts0_us),
                horizon_minutes=int(self.cfg.horizon_minutes),
                n=int(n),
                ic_spearman=ic_s,
                ic_pearson=ic_p,
                ls_ret=float(ls_ret),
                top_bottom_spread=float(spread),
                coverage=float(coverage),
                score_min=score_min,
                score_p5=score_p5,
                score_mean=score_mean,
                score_p95=score_p95,
                score_max=score_max,
                meta=meta,
            )
        )

    @staticmethod
    def _corr(a: np.ndarray, b: np.ndarray) -> float:
        a = np.asarray(a, dtype=np.float64)
        b = np.asarray(b, dtype=np.float64)

        if a.size < 2:
            return float("nan")

        am = float(np.mean(a))
        bm = float(np.mean(b))
        da = a - am
        db = b - bm

        va = float(np.mean(da * da))
        vb = float(np.mean(db * db))
        if not np.isfinite(va) or not np.isfinite(vb) or va <= 0.0 or vb <= 0.0:
            return float("nan")

        cov = float(np.mean(da * db))
        return float(cov / np.sqrt(va * vb))

    @staticmethod
    def _long_short_quantile(
        *,
        x: np.ndarray,
        y: np.ndarray,
        q: int,
    ) -> tuple[float, float]:
        if q < 2:
            raise ValueError("quantiles must be >= 2")
        n = int(x.size)
        if n == 0:
            return float("nan"), float("nan")

        k = max(1, n // q)

        # argsort once
        idx = np.argsort(x, kind="mergesort")
        bot = idx[:k]
        top = idx[-k:]

        ret_bot = float(np.mean(y[bot]))
        ret_top = float(np.mean(y[top]))

        spread = ret_top - ret_bot
        return float(spread), float(spread)

    @staticmethod
    def _ir(arr: np.ndarray) -> float:
        arr = np.asarray(arr, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size < 2:
            return float("nan")
        m = float(np.mean(arr))
        s = float(np.std(arr, ddof=1))
        return float(m / s) if s > 0 else float("nan")

    @staticmethod
    def _tstat(arr: np.ndarray) -> float:
        arr = np.asarray(arr, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size < 2:
            return float("nan")
        m = float(np.mean(arr))
        s = float(np.std(arr, ddof=1))
        if s <= 0:
            return float("nan")
        return float(m / (s / np.sqrt(arr.size)))
