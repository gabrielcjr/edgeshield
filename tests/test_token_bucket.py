"""Tests for Token Bucket algorithm with dynamic refill."""

import asyncio
import pytest
from src.config import TierPolicy
from src.limiters.memory_limiter import MemoryRateLimiter
from src.limiters.redis_limiter import RedisRateLimiter


@pytest.mark.asyncio
async def test_memory_token_bucket_burst_and_exhaustion(sample_token_policy):
    limiter = MemoryRateLimiter()
    key = "tb:mem:user1"

    # Burst capacity is 3
    res1 = await limiter.check(key, sample_token_policy)
    assert res1.allowed is True
    assert res1.remaining == 2

    res2 = await limiter.check(key, sample_token_policy)
    assert res2.allowed is True
    assert res2.remaining == 1

    res3 = await limiter.check(key, sample_token_policy)
    assert res3.allowed is True
    assert res3.remaining == 0

    # 4th request must be rejected
    res4 = await limiter.check(key, sample_token_policy)
    assert res4.allowed is False
    assert res4.remaining == 0
    assert res4.reset_after_seconds > 0.0


@pytest.mark.asyncio
async def test_redis_token_bucket_burst_and_refill(fake_redis, sample_token_policy):
    limiter = RedisRateLimiter(fake_redis)
    key = "tb:redis:user2"

    # Burst 3 requests
    for i in range(3):
        res = await limiter.check(key, sample_token_policy)
        assert res.allowed is True

    # Next request must be rejected
    blocked = await limiter.check(key, sample_token_policy)
    assert blocked.allowed is False

    # Wait 1.1 seconds for 1 token to refill (refill_rate = 1.0 token/sec)
    await asyncio.sleep(1.1)

    refilled = await limiter.check(key, sample_token_policy)
    assert refilled.allowed is True
