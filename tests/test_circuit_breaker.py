"""Tests for 3-state Circuit Breaker."""

import asyncio
import pytest
from src.core.circuit_breaker import CircuitBreaker, CircuitState
from src.core.errors import CircuitBreakerOpenError


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_to_open_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2, half_open_probes=2)
    assert cb.state == CircuitState.CLOSED

    async def failing_operation():
        raise ConnectionError("Simulated Redis Failure")

    # 1st failure
    with pytest.raises(ConnectionError):
        await cb.call(failing_operation)
    assert cb.state == CircuitState.CLOSED

    # 2nd failure
    with pytest.raises(ConnectionError):
        await cb.call(failing_operation)
    assert cb.state == CircuitState.CLOSED

    # 3rd failure: trips to OPEN
    with pytest.raises(ConnectionError):
        await cb.call(failing_operation)
    assert cb.state == CircuitState.OPEN

    # Subsequent call fast-fails with CircuitBreakerOpenError without calling function
    called = False

    async def probe():
        nonlocal called
        called = True

    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(probe)
    assert called is False


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_to_half_open_and_closed():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_probes=2)

    # Force open
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # 1st successful probe
    await cb.call(lambda: asyncio.sleep(0.001))
    assert cb.state == CircuitState.HALF_OPEN

    # 2nd successful probe completes recovery
    await cb.call(lambda: asyncio.sleep(0.001))
    assert cb.state == CircuitState.CLOSED
