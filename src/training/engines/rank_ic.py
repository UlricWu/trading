# filepath: src/training/engines/rank_ic.py
"""Compute Rank IC from fixed, row-aligned predictions and labels."""

from __future__ import annotations

import numpy as np
from scipy.stats import rankdata


def rank_ic(
    predictions: np.ndarray,
    labels: np.ndarray,
) -> tuple[float | None, int, tuple[str, ...]]:
    """Use finite pairs and return every applicable mathematical undefined reason.

    NaN represents ordinary missingness; infinity and malformed arrays are errors.

    Example:
        value, pair_count, reasons = rank_ic(
            np.array([0.0, np.nan, 2.0]), np.array([1.0, 2.0, 3.0])
        )
        assert (value, pair_count, reasons) == (1.0, 2, ())
    """
    predictions = np.asarray(predictions, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    if predictions.ndim != 1 or labels.ndim != 1 or predictions.shape != labels.shape:
        raise ValueError("predictions and labels must be aligned one-dimensional arrays")
    if np.isinf(predictions).any() or np.isinf(labels).any():
        raise ValueError("predictions and labels must not contain infinity")
    paired = np.isfinite(predictions) & np.isfinite(labels)
    x = predictions[paired]
    y = labels[paired]
    pair_count = int(paired.sum())
    reasons: list[str] = []
    if pair_count < 2:
        reasons.append("insufficient_pairs")
    if pair_count > 0 and np.all(x == x[0]):
        reasons.append("constant_prediction")
    if pair_count > 0 and np.all(y == y[0]):
        reasons.append("constant_label")
    if reasons:
        return None, pair_count, tuple(reasons)
    x_rank = rankdata(x, method="average")
    y_rank = rankdata(y, method="average")
    x_centered = x_rank - x_rank.mean()
    y_centered = y_rank - y_rank.mean()
    denominator = np.sqrt(np.dot(x_centered, x_centered) * np.dot(y_centered, y_centered))
    value = float(np.dot(x_centered, y_centered) / denominator)
    if not np.isfinite(value):
        raise RuntimeError("Rank IC computation returned an unexplained non-finite result")
    return value, pair_count, ()
