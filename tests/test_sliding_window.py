"""Tests for Sliding Window Counter algorithm (Memory and Pure Python Redis)."""

import pytest
from src.config import TierPolicy
from src.limiters.memory_limiter import MemoryRateLimiter
from src.limiters.redis_limiter import RedisRateLimiter


@pytest.mark.asyncio
async def test_memory_sliding_window_allows_up_to_limit(sample_sliding_policy):
    limiter = MemoryRateLimiter()
    key = "user:123"

    # Consume 5 allowed requests
    for i in range(5):
        result = await limiter.check(key, sample_sliding_policy)
        assert result.allowed is True
        assert result.remaining == (5 - i - 1)
        assert result.is_fallback is True

    # 6th request must be rejected
    rejected = await limiter.check(key, sample_sliding_policy)
    assert rejected.allowed is False
    assert rejected.remaining == 0
    assert rejected.reset_after_seconds > 0.0


@pytest.mark.asyncio
async def test_redis_sliding_window_allows_up_to_limit(fake_redis, sample_sliding_policy):
    limiter = RedisRateLimiter(fake_redis)
    key = "user:456"

    # Consume 5 allowed requests
    for i in range(5):
        result = await limiter.check(key, sample_sliding_policy)
        assert result.allowed is True
        assert result.remaining == (5 - i - 1)
        assert result.is_fallback is False

    # 6th request must be rejected
    rejected = await limiter.check(key, sample_sliding_policy)
    assert rejected.allowed is False
    assert rejected.remaining == 0
    assert rejected.reset_after_seconds > 0.0


@pytest.mark.asyncio
async def test_sliding_window_independent_keys(fake_redis, sample_sliding_policy):
    limiter = RedisRateLimiter(fake_redis)

    # Exhaust key A
    for _ in range(5):
        res = await limiter.check("client_a", sample_sliding_policy)
        assert res.allowed is True

    assert (await limiter.check("client_a", sample_sliding_policy)).allowed is False

    # Key B must still have full quota
    res_b = await limiter.check("client_b", sample_sliding_policy)
    assert res_b.allowed is True
    assert res_b.remaining == 4
