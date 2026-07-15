# filepath: tests/utils/test_retry.py
from __future__ import annotations

import asyncio
import math

import pytest

from src.utils.retry import AsyncRetry, Retry, RetryPolicy


def test_retry_returns_success_after_configured_backoff() -> None:
    attempts = 0
    waits: list[float] = []
    policy = RetryPolicy(
        exceptions=(OSError,),
        max_attempts=3,
        initial_delay_seconds=2.0,
        backoff_multiplier=3.0,
    )

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("temporary")
        return "ready"

    result = Retry.run(operation, policy=policy, sleep=waits.append)

    assert result == "ready"
    assert attempts == 3
    assert waits == [2.0, 6.0]


def test_retry_reraises_final_matching_exception() -> None:
    policy = RetryPolicy(
        exceptions=(OSError,),
        max_attempts=2,
        initial_delay_seconds=0.0,
    )

    def operation() -> str:
        raise OSError("still unavailable")

    with pytest.raises(OSError, match="still unavailable"):
        Retry.run(operation, policy=policy, sleep=lambda _wait_seconds: None)


def test_retry_requires_explicit_random_source_for_jitter() -> None:
    policy = RetryPolicy(exceptions=(OSError,), jitter_ratio=0.2)

    with pytest.raises(ValueError, match="random_source"):
        Retry.run(lambda: "unused", policy=policy)


def test_retry_policy_rejects_broad_exception_type() -> None:
    with pytest.raises(ValueError, match="specific Exception subclasses"):
        RetryPolicy(exceptions=(Exception,))


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
def test_retry_policy_rejects_non_finite_delay(invalid_value: float) -> None:
    with pytest.raises(ValueError, match="initial_delay_seconds"):
        RetryPolicy(
            exceptions=(OSError,),
            initial_delay_seconds=invalid_value,
        )


def test_retry_decorator_preserves_arguments_and_result() -> None:
    policy = RetryPolicy(exceptions=(OSError,), max_attempts=1)

    @Retry.decorator(policy=policy)
    def add(left: int, right: int) -> int:
        return left + right

    assert add(2, 3) == 5


def test_async_retry_uses_injected_async_sleep() -> None:
    async def scenario() -> tuple[str, list[float]]:
        attempts = 0
        waits: list[float] = []
        policy = RetryPolicy(
            exceptions=(OSError,),
            max_attempts=2,
            initial_delay_seconds=1.5,
        )

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary")
            return "ready"

        async def record_sleep(wait_seconds: float) -> None:
            waits.append(wait_seconds)

        result = await AsyncRetry.run(
            operation,
            policy=policy,
            sleep=record_sleep,
        )
        return result, waits

    assert asyncio.run(scenario()) == ("ready", [1.5])


def test_async_retry_decorator_preserves_async_signature() -> None:
    @AsyncRetry.decorator(policy=RetryPolicy(exceptions=(OSError,), max_attempts=1))
    async def double(value: int) -> int:
        return value * 2

    async def scenario() -> int:
        return await double(4)

    assert asyncio.run(scenario()) == 8
