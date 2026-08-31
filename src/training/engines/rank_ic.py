# filepath: src/training/engines/rank_ic.py
"""Calculate and summarize Rank IC for offline training evaluation."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.training.inference_model import InferenceModel


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
    model: InferenceModel,
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
    if len(eval_X) != len(eval_y):
        raise RuntimeError("Rank IC eval_X / eval_y length mismatch")
    if not eval_X.index.equals(eval_y.index):
        raise RuntimeError("Rank IC eval_X / eval_y index mismatch")
    if tuple(eval_X.columns) != model.feature_names:
        raise ValueError(
            "Rank IC feature columns must match fitted feature_names exactly"
        )

    keep_rows, predictions = model.predict(
        eval_X.to_numpy(dtype=float, na_value=np.nan, copy=True)
    )
    labels = eval_y.to_numpy(dtype=float, na_value=np.nan, copy=True)[keep_rows]
    if labels.shape[0] < 2:
        raise RuntimeError("Rank IC requires at least two retained evaluation rows")
    if not np.isfinite(labels).all():
        raise ValueError("Rank IC labels must be finite")

    rank_ic, _ = spearmanr(predictions, labels)
    value = float(rank_ic)
    if not math.isfinite(value):
        raise RuntimeError("Rank IC must be finite")
    return value


def build_rank_ic_rows(metrics: Mapping[str, float]) -> tuple[RankICRow, ...]:
    """Build an ordered Rank IC series from validated artifact metrics.

    Example:
        rows = build_rank_ic_rows({"ic@2026-07-20": 0.1})
    """
    pairs = sorted((key.removeprefix("ic@"), value) for key, value in metrics.items())
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
