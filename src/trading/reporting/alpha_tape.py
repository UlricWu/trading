# filepath: src/trading/reporting/alpha_tape.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class AlphaPoint:
    ts_us: int
    horizon_minutes: int
    n: int
    ic_spearman: float
    ic_pearson: float
    ls_ret: float
    top_bottom_spread: float
    coverage: float
    score_min: float
    score_p5: float
    score_mean: float
    score_p95: float
    score_max: float
    meta: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.meta is not None:
            object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(slots=True)
class AlphaTape:
    points: list[AlphaPoint]

    def append(self, p: AlphaPoint) -> None:
        self.points.append(p)

    def to_rows(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for p in self.points:
            out.append(
                {
                    "ts_us": p.ts_us,
                    "horizon_minutes": p.horizon_minutes,
                    "n": p.n,
                    "ic_spearman": p.ic_spearman,
                    "ic_pearson": p.ic_pearson,
                    "ls_ret": p.ls_ret,
                    "top_bottom_spread": p.top_bottom_spread,
                    "coverage": p.coverage,
                    "score_min": p.score_min,
                    "score_p5": p.score_p5,
                    "score_mean": p.score_mean,
                    "score_p95": p.score_p95,
                    "score_max": p.score_max,
                    "meta": p.meta or {},
                }
            )
        return out
