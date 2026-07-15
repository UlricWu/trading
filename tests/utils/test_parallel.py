# filepath: tests/utils/test_parallel.py
from __future__ import annotations

import pytest

from src.utils.parallel import ParallelExecutor


def _double(value: int) -> int:
    return value * 2


def test_parallel_executor_returns_empty_list_for_empty_input() -> None:
    assert ParallelExecutor.run(items=(), handler=_double) == []


def test_parallel_executor_preserves_order_with_one_worker() -> None:
    assert ParallelExecutor.run(
        items=(3, 1, 2),
        handler=_double,
        max_workers=1,
    ) == [6, 2, 4]


def test_parallel_executor_collects_process_results() -> None:
    results = ParallelExecutor.run(
        items=(1, 2, 3),
        handler=_double,
        max_workers=2,
    )

    assert sorted(results) == [2, 4, 6]


@pytest.mark.parametrize(
    ("max_workers", "error_type"),
    [(0, ValueError), (-1, ValueError), (True, TypeError)],
)
def test_parallel_executor_rejects_invalid_worker_counts(
    max_workers: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type, match="max_workers"):
        ParallelExecutor.run(
            items=(1,),
            handler=_double,
            # Deliberately violate the static contract to verify boundary validation.
            max_workers=max_workers,  # type: ignore[arg-type]
        )


def test_parallel_executor_rejects_non_callable_handler() -> None:
    with pytest.raises(TypeError, match="handler"):
        ParallelExecutor.run(
            items=(1,),
            handler=object(),  # type: ignore[arg-type]
        )
