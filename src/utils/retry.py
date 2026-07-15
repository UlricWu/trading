# filepath: src/utils/retry.py
from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import ParamSpec, TypeVar

_Parameters = ParamSpec("_Parameters")
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Validated retry attempts, delays, backoff, jitter, and exceptions."""

    exceptions: tuple[type[Exception], ...]
    max_attempts: int = 3
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.exceptions, tuple)
            or not self.exceptions
            or any(
                not isinstance(exception_type, type)
                or not issubclass(exception_type, Exception)
                or exception_type is Exception
                for exception_type in self.exceptions
            )
        ):
            raise ValueError(
                "field 'exceptions' must contain specific Exception subclasses"
            )
        if type(self.max_attempts) is not int or self.max_attempts <= 0:
            raise ValueError("field 'max_attempts' must be a positive integer")
        initial_delay_seconds = _require_finite_number(
            self.initial_delay_seconds,
            field_name="initial_delay_seconds",
        )
        if initial_delay_seconds < 0:
            raise ValueError("field 'initial_delay_seconds' must be non-negative")
        backoff_multiplier = _require_finite_number(
            self.backoff_multiplier,
            field_name="backoff_multiplier",
        )
        if backoff_multiplier < 1:
            raise ValueError("field 'backoff_multiplier' must be at least 1")
        jitter_ratio = _require_finite_number(
            self.jitter_ratio,
            field_name="jitter_ratio",
        )
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("field 'jitter_ratio' must be between 0 and 1")

        object.__setattr__(self, "initial_delay_seconds", initial_delay_seconds)
        object.__setattr__(self, "backoff_multiplier", backoff_multiplier)
        object.__setattr__(self, "jitter_ratio", jitter_ratio)


class Retry:
    """Retry a synchronous zero-argument operation under an explicit policy."""

    @staticmethod
    def run(
        operation: Callable[[], _ResultT],
        *,
        policy: RetryPolicy,
        random_source: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> _ResultT:
        """Return the first success or re-raise the final matching exception."""
        if not callable(operation):
            raise TypeError("field 'operation' must be callable")
        if not callable(sleep):
            raise TypeError("field 'sleep' must be callable")
        _validate_random_source(policy=policy, random_source=random_source)
        attempt_number = 1
        while True:
            try:
                return operation()
            except policy.exceptions:
                if attempt_number == policy.max_attempts:
                    raise
                sleep(
                    _calculate_wait_seconds(
                        policy=policy,
                        attempt_number=attempt_number,
                        random_source=random_source,
                    )
                )
                attempt_number += 1

    @staticmethod
    def decorator(
        *,
        policy: RetryPolicy,
        random_source: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Callable[
        [Callable[_Parameters, _ResultT]],
        Callable[_Parameters, _ResultT],
    ]:
        """Apply the same synchronous retry contract to a callable signature."""
        if not callable(sleep):
            raise TypeError("field 'sleep' must be callable")
        _validate_random_source(policy=policy, random_source=random_source)

        def decorate(
            function: Callable[_Parameters, _ResultT],
        ) -> Callable[_Parameters, _ResultT]:
            @wraps(function)
            def wrapped(
                *args: _Parameters.args,
                **kwargs: _Parameters.kwargs,
            ) -> _ResultT:
                return Retry.run(
                    lambda: function(*args, **kwargs),
                    policy=policy,
                    random_source=random_source,
                    sleep=sleep,
                )

            return wrapped

        return decorate


class AsyncRetry:
    """Retry an asynchronous zero-argument operation under an explicit policy."""

    @staticmethod
    async def run(
        operation: Callable[[], Awaitable[_ResultT]],
        *,
        policy: RetryPolicy,
        random_source: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> _ResultT:
        """Return the first async success or re-raise the final exception."""
        if not callable(operation):
            raise TypeError("field 'operation' must be callable")
        if not callable(sleep):
            raise TypeError("field 'sleep' must be callable")
        _validate_random_source(policy=policy, random_source=random_source)
        attempt_number = 1
        while True:
            try:
                return await operation()
            except policy.exceptions:
                if attempt_number == policy.max_attempts:
                    raise
                await sleep(
                    _calculate_wait_seconds(
                        policy=policy,
                        attempt_number=attempt_number,
                        random_source=random_source,
                    )
                )
                attempt_number += 1

    @staticmethod
    def decorator(
        *,
        policy: RetryPolicy,
        random_source: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> Callable[
        [Callable[_Parameters, Awaitable[_ResultT]]],
        Callable[_Parameters, Awaitable[_ResultT]],
    ]:
        """Apply the same asynchronous retry contract to a callable signature."""
        if not callable(sleep):
            raise TypeError("field 'sleep' must be callable")
        _validate_random_source(policy=policy, random_source=random_source)

        def decorate(
            function: Callable[_Parameters, Awaitable[_ResultT]],
        ) -> Callable[_Parameters, Awaitable[_ResultT]]:
            @wraps(function)
            async def wrapped(
                *args: _Parameters.args,
                **kwargs: _Parameters.kwargs,
            ) -> _ResultT:
                return await AsyncRetry.run(
                    lambda: function(*args, **kwargs),
                    policy=policy,
                    random_source=random_source,
                    sleep=sleep,
                )

            return wrapped

        return decorate


def _validate_random_source(
    *,
    policy: RetryPolicy,
    random_source: random.Random | None,
) -> None:
    if random_source is not None and not isinstance(random_source, random.Random):
        raise TypeError("field 'random_source' must be random.Random or None")
    if policy.jitter_ratio > 0 and random_source is None:
        raise ValueError(
            "field 'random_source' is required when jitter_ratio is positive"
        )


def _require_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"field '{field_name}' must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"field '{field_name}' must be finite")
    return float(value)


def _calculate_wait_seconds(
    *,
    policy: RetryPolicy,
    attempt_number: int,
    random_source: random.Random | None,
) -> float:
    wait_seconds = policy.initial_delay_seconds * (
        policy.backoff_multiplier ** (attempt_number - 1)
    )
    if random_source is None:
        return wait_seconds
    jitter_multiplier = random_source.uniform(
        1 - policy.jitter_ratio,
        1 + policy.jitter_ratio,
    )
    return wait_seconds * jitter_multiplier


__all__ = ["AsyncRetry", "Retry", "RetryPolicy"]
