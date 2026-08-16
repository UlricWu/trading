# filepath: src/training/engines/rank_ic.py
"""Calculate and summarize Rank IC for offline training evaluation."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd
from scipy.stats import spearmanr

from src.training.inference_model import PredictionModel
from src.utils import table_ops

_IC_KEY = re.compile(r"^ic@(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True, slots=True)
class RankICRow:
    """Describe one dated Rank IC observation and its rolling means.

    Example:
        row = RankICRow(
            eval_date="2026-07-20",
            rank_ic=0.1,
            rolling_5=None,
            rolling_20=None,
        )
    """

    eval_date: str
    rank_ic: float
    rolling_5: float | None
    rolling_20: float | None


@dataclass(frozen=True, slots=True)
class RankICSummary:
    """Summarize the complete ordered Rank IC series.

    Example:
        summary = RankICSummary(
            observations=1,
            eval_start="2026-07-20",
            eval_end="2026-07-20",
            mean_ic=0.1,
            median_ic=0.1,
            std_ic=None,
            t_stat=None,
            positive_ratio=1.0,
            min_ic=0.1,
            min_ic_date="2026-07-20",
            max_ic=0.1,
            max_ic_date="2026-07-20",
            last_5_mean=0.1,
            rolling_20_last=None,
        )
    """

    observations: int
    eval_start: str
    eval_end: str
    mean_ic: float
    median_ic: float
    std_ic: float | None
    t_stat: float | None
    positive_ratio: float
    min_ic: float
    min_ic_date: str
    max_ic: float
    max_ic_date: str
    last_5_mean: float
    rolling_20_last: float | None


def evaluate_rank_ic(
    *,
    model: PredictionModel,
    eval_X: pd.DataFrame,
    eval_y: pd.Series,
) -> float:
    """Return Rank IC for one model and aligned evaluation partition.

    Example:
        rank_ic = evaluate_rank_ic(
            model=model,
            eval_X=eval_X,
            eval_y=eval_y,
        )
    """
    table_ops.require_nonempty(eval_X, who="Rank IC eval_X")
    if len(eval_X) != len(eval_y):
        raise RuntimeError("Rank IC eval_X / eval_y length mismatch")
    if not eval_X.index.equals(eval_y.index):
        raise RuntimeError("Rank IC eval_X / eval_y index mismatch")
    predictions = model.predict(eval_X.values)
    rank_ic, _ = spearmanr(predictions, eval_y.values)
    return float(rank_ic)


def build_rank_ic_rows(metrics: Mapping[str, object]) -> tuple[RankICRow, ...]:
    """Parse persisted metrics into an ordered Rank IC series.

    Example:
        rows = build_rank_ic_rows({"ic@2026-07-20": 0.1})
    """
    pairs: list[tuple[str, float]] = []
    for key, raw_value in metrics.items():
        match = _IC_KEY.fullmatch(str(key))
        if match is None:
            continue

        eval_date = match.group(1)
        try:
            date.fromisoformat(eval_date)
        except ValueError as exc:
            raise ValueError(f"invalid training metric date: {key}") from exc
        if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
            raise TypeError(f"training metric {key} must be a finite number")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"training metric {key} must be a finite number")
        pairs.append((eval_date, value))

    if not pairs:
        raise ValueError("training metrics must contain ic@YYYY-MM-DD values")

    pairs.sort(key=lambda item: item[0])
    values = [value for _, value in pairs]
    rolling_5 = _rolling_means(values, 5)
    rolling_20 = _rolling_means(values, 20)
    return tuple(
        RankICRow(
            eval_date=eval_date,
            rank_ic=value,
            rolling_5=rolling_5[index],
            rolling_20=rolling_20[index],
        )
        for index, (eval_date, value) in enumerate(pairs)
    )


def summarize_rank_ic(rows: Sequence[RankICRow]) -> RankICSummary:
    """Return summary statistics for a non-empty ordered Rank IC series.

    Example:
        summary = summarize_rank_ic(
            (
                RankICRow(
                    eval_date="2026-07-20",
                    rank_ic=0.1,
                    rolling_5=None,
                    rolling_20=None,
                ),
            )
        )
    """
    values = [row.rank_ic for row in rows]
    if not values:
        raise ValueError("Rank IC rows must not be empty")
    count = len(values)
    mean_ic = sum(values) / count
    std_ic = statistics.stdev(values) if count > 1 else None
    t_stat = (
        mean_ic / (std_ic / math.sqrt(count))
        if std_ic is not None and std_ic > 0.0
        else None
    )
    min_row = min(rows, key=lambda row: row.rank_ic)
    max_row = max(rows, key=lambda row: row.rank_ic)
    return RankICSummary(
        observations=count,
        eval_start=rows[0].eval_date,
        eval_end=rows[-1].eval_date,
        mean_ic=mean_ic,
        median_ic=statistics.median(values),
        std_ic=std_ic,
        t_stat=t_stat,
        positive_ratio=sum(1 for value in values if value > 0.0) / count,
        min_ic=min_row.rank_ic,
        min_ic_date=min_row.eval_date,
        max_ic=max_row.rank_ic,
        max_ic_date=max_row.eval_date,
        last_5_mean=sum(values[-5:]) / min(5, count),
        rolling_20_last=rows[-1].rolling_20,
    )


def _rolling_means(values: Sequence[float], window: int) -> list[float | None]:
    rolling: list[float | None] = []
    for index in range(len(values)):
        start = index - window + 1
        if start < 0:
            rolling.append(None)
        else:
            rolling.append(sum(values[start : index + 1]) / window)
    return rolling
