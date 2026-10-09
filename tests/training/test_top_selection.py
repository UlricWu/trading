# filepath: tests/training/test_top_selection.py
"""Frozen top selection and evaluable-label denominator boundaries."""

import numpy as np

from src.training.top_selection import evaluate_top_selection, select_top_symbols


def test_unknown_top_score_is_not_replaced_and_point_nine_is_not_an_event() -> None:
    selected = select_top_symbols(("b", "a", "c"), np.array([0.8, 0.8, 0.7]), top_k=2)
    np.testing.assert_array_equal(selected, [1, 0])
    metrics = evaluate_top_selection(ranks=np.array([1.0, np.nan, 0.9]), selected=selected)
    assert metrics.selected == 2 and metrics.selected_known == metrics.hits == 1
    assert metrics.precision == metrics.recall == metrics.f1 == 1.0
    assert metrics.random_precision == 0.5 and metrics.lift == 2.0
    assert metrics.unknown_selected == 1 and metrics.precision_lower == 0.5


def test_empty_evaluable_selection_has_undefined_precision() -> None:
    metrics = evaluate_top_selection(ranks=np.array([np.nan, 1.0]), selected=np.array([0]))
    assert metrics.precision is None and metrics.lift is None
    assert metrics.recall == 0.0 and metrics.f1 == 0.0


def test_fifty_selected_ten_unknown_eight_hits_is_twenty_percent() -> None:
    ranks = np.array([1.0] * 8 + [0.5] * 32 + [np.nan] * 10 + [1.0])
    metrics = evaluate_top_selection(ranks=ranks, selected=np.arange(50))
    assert metrics.precision == 0.2
    assert metrics.hits == 8 and metrics.selected_known == 40
