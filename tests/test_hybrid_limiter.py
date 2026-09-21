"""Tests for Hybrid Limiter with Circuit Breaker and In-Memory Fallback."""

import pytest
from unittest.mock import AsyncMock
from src.config import TierPolicy
from src.core.circuit_breaker import CircuitBreaker, CircuitState
from src.limiters.hybrid_limiter import HybridRateLimiter
from src.limiters.memory_limiter import MemoryRateLimiter
from src.limiters.redis_limiter import RedisRateLimiter


@pytest.mark.asyncio
async def test_hybrid_limiter_falls_back_when_redis_fails(sample_sliding_policy):
    # Mock RedisRateLimiter that raises ConnectionError on check()
    mock_redis_limiter = AsyncMock(spec=RedisRateLimiter)
    mock_redis_limiter.check.side_effect = ConnectionError("Redis cluster unreachable")

    circuit_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1.0)
    memory_limiter = MemoryRateLimiter()

    hybrid = HybridRateLimiter(
        redis_client=None,
        circuit_breaker=circuit_breaker,
        memory_limiter=memory_limiter,
    )
    # Inject mock redis limiter
    hybrid.redis_limiter = mock_redis_limiter

    # 1st call fails Redis, trips fallback to memory
    res1 = await hybrid.check("fallback_user", sample_sliding_policy)
    assert res1.allowed is True
    assert res1.is_fallback is True

    # 2nd call fails Redis, trips CB to OPEN
    res2 = await hybrid.check("fallback_user", sample_sliding_policy)
    assert res2.allowed is True
    assert res2.is_fallback is True
    assert circuit_breaker.state == CircuitState.OPEN

    # 3rd call immediately fast-fails Redis (CB is OPEN) and evaluates via memory limiter
    res3 = await hybrid.check("fallback_user", sample_sliding_policy)
    assert res3.allowed is True
    assert res3.is_fallback is True
    assert res3.remaining == 2  # 5 - 3 requests = 2 remaining in memory limiter
