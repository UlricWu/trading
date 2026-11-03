# filepath: tests/training/engines/test_rank_ic.py
import numpy as np
import pytest

from src.training.engines.rank_ic import rank_ic


@pytest.mark.parametrize(("x", "y", "expected"), [
    ([0.0, 1.0, 2.0], [3.0, 2.0, 1.0], (-1.0, 3, ())),
    ([0.0, np.nan, 2.0, 3.0], [1.0, 2.0, np.nan, 3.0], (1.0, 2, ())),
    ([], [], (None, 0, ("insufficient_pairs",))),
    ([1.0], [2.0], (None, 1, ("insufficient_pairs", "constant_prediction", "constant_label"))),
    ([1.0, 1.0], [2.0, 3.0], (None, 2, ("constant_prediction",))),
    ([1.0, 2.0], [3.0, 3.0], (None, 2, ("constant_label",))),
    ([1.0, 1.0], [2.0, 2.0], (None, 2, ("constant_prediction", "constant_label"))),
])
def test_exact_value_pairs_and_all_undefined_reasons(x: list[float], y: list[float], expected: tuple[float | None, int, tuple[str, ...]]) -> None:
    assert rank_ic(np.array(x), np.array(y)) == expected


def test_ties_use_average_ranks() -> None:
    value, pairs, reasons = rank_ic(np.array([0.0, 1.0, 1.0, 3.0]), np.array([1.0, 2.0, 3.0, 4.0]))
    assert value == pytest.approx(0.9486832980505139)
    assert (pairs, reasons) == (4, ())


@pytest.mark.parametrize(("x", "y"), [([np.inf], [1.0]), ([1.0], [-np.inf]), ([1.0], []), ([[1.0]], [1.0])])
def test_invalid_inputs_are_errors(x: object, y: object) -> None:
    with pytest.raises(ValueError):
        rank_ic(np.array(x), np.array(y))


def test_unexplained_nonfinite_calculation_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.training.engines.rank_ic.rankdata", lambda values, **kwargs: np.full(len(values), np.nan))
    with pytest.raises(RuntimeError, match="unexplained"):
        rank_ic(np.array([0.0, 1.0]), np.array([1.0, 2.0]))
