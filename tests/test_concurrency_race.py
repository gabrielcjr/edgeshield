"""Concurrency and Race Condition Stress Tests."""

import asyncio
import pytest
from src.config import TierPolicy
from src.limiters.redis_limiter import RedisRateLimiter


@pytest.mark.asyncio
async def test_sliding_window_concurrency_race_condition_safety(fake_redis):
    """
    Spawns 50 simultaneous concurrent coroutines targeting the same key
    with a strict quota limit of 10.
    Verifies that exactly 10 requests succeed and exactly 40 are rejected.
    """
    limiter = RedisRateLimiter(fake_redis)
    policy = TierPolicy(name="concurrency_test", limit=10, window_seconds=60)
    key = "concurrent_client_test"

    async def make_request():
        return await limiter.check(key, policy, cost=1)

    # Launch 50 concurrent requests simultaneously
    results = await asyncio.gather(*(make_request() for _ in range(50)))

    allowed_count = sum(1 for r in results if r.allowed)
    blocked_count = sum(1 for r in results if not r.allowed)

    assert allowed_count == 10, f"Expected exactly 10 allowed requests, got {allowed_count}"
    assert blocked_count == 40, f"Expected exactly 40 blocked requests, got {blocked_count}"
