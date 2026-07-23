# filepath: src/trading/portfolio/constructors/topk_hysteresis.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math

from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.state import PortfolioState


@dataclass(slots=True)
class TopKHysteresisConstructor(PortfolioConstructor):
    """
    Select top-k signals while retaining eligible previous holdings.

    Ideal-world logic:
    - ranks scores
    - selects up to max_positions above entry_threshold
    - exits only when below exit_threshold
    - optionally keep_winners within the max_positions cap

    No institutional constraints here (T+1/limits/cash).
    """
    max_positions: int
    entry_threshold: float
    exit_threshold: float
    rebalance_interval_minutes: int = 1
    keep_winners: bool = False
    target_quantity: int = 100

    _last_rebalance_minute: int | None = None
    _last_targets: dict[str, int] = field(default_factory=dict)

    def name(self) -> str:
        """Return the public constructor type."""
        return "topk_hysteresis"

    def targets(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        state: PortfolioState,
    ) -> dict[str, int]:
        """Return hysteresis-aware targets for one monotonic timestamp."""
        minute = int(ts_us) // 60_000_000
        if self._last_rebalance_minute is not None:
            if minute < self._last_rebalance_minute:
                raise ValueError("ts_us must be monotonic across target calls")
            if (
                minute - self._last_rebalance_minute
                < self.rebalance_interval_minutes
            ):
                cached = dict(self._last_targets)
                for symbol in scores:
                    cached.setdefault(str(symbol), 0)
                return cached

        self._last_rebalance_minute = minute

        score_map = {
            str(symbol): float(score)
            for symbol, score in scores.items()
        }
        invalid_scores = [
            symbol for symbol, score in score_map.items() if not math.isfinite(score)
        ]
        if invalid_scores:
            raise ValueError(f"scores must be finite: {invalid_scores}")
        ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        max_positions = self.max_positions
        target_quantity = self.target_quantity

        selected: dict[str, int] = {}
        if self.keep_winners:
            held_winners = sorted(
                (
                    (str(symbol), score_map[str(symbol)], int(pos_qty))
                    for symbol, pos_qty in (state.positions or {}).items()
                    if int(pos_qty) > 0
                    and str(symbol) in score_map
                    and score_map[str(symbol)] > self.exit_threshold
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            for symbol, _score, pos_qty in held_winners:
                if len(selected) >= max_positions:
                    break
                selected[symbol] = pos_qty

        for symbol, score in ranked:
            if len(selected) >= max_positions:
                break
            if score >= self.entry_threshold:
                selected.setdefault(symbol, target_quantity)

        targets: dict[str, int] = dict(selected)
        for symbol in score_map:
            targets.setdefault(symbol, 0)
        for symbol, pos_qty in (state.positions or {}).items():
            if int(pos_qty) > 0:
                targets.setdefault(str(symbol), 0)

        self._last_targets = dict(targets)
        return targets
