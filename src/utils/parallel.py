# filepath: src/utils/parallel.py
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TypeVar

_ItemT = TypeVar("_ItemT")
_ResultT = TypeVar("_ResultT")


class ParallelExecutor:
    """Execute independent items sequentially or in worker processes.

    Results preserve input order only when one worker is selected. Parallel
    results are returned in completion order.
    """

    @staticmethod
    def run(
        *,
        items: Iterable[_ItemT],
        handler: Callable[[_ItemT], _ResultT],
        max_workers: int | None = None,
    ) -> list[_ResultT]:
        """Return handler results, using an empty list for empty input."""
        if not callable(handler):
            raise TypeError("field 'handler' must be callable")
        if max_workers is not None and type(max_workers) is not int:
            raise TypeError("field 'max_workers' must be an integer or None")
        if max_workers is not None and max_workers <= 0:
            raise ValueError("field 'max_workers' must be positive")

        owned_items = list(items)
        if not owned_items:
            return []

        requested_worker_count = (
            (os.cpu_count() or 1) if max_workers is None else max_workers
        )
        worker_count = min(requested_worker_count, len(owned_items))
        if worker_count == 1:
            return [handler(item) for item in owned_items]
        return ParallelExecutor._run_in_processes(
            items=owned_items,
            handler=handler,
            worker_count=worker_count,
        )

    @staticmethod
    def _run_in_processes(
        *,
        items: Sequence[_ItemT],
        handler: Callable[[_ItemT], _ResultT],
        worker_count: int,
    ) -> list[_ResultT]:
        """Own the process-pool lifecycle and collect completion-order results."""
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(handler, item) for item in items]
            return [future.result() for future in as_completed(futures)]


__all__ = ["ParallelExecutor"]
