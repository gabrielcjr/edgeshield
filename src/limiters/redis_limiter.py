"""Pure Python Distributed Rate Limiter using Async Redis Pipelines & Atomic Operations."""

import time
import uuid
from typing import Optional
import redis.asyncio as aioredis

from src.config import TierPolicy
from src.core.policies import RateLimitResult
from src.limiters.base import BaseRateLimiter
from src.telemetry.otel import redis_latency_histogram, tracer


class RedisRateLimiter(BaseRateLimiter):
    """
    Pure Python asynchronous distributed Rate Limiter using atomic Redis Pipelines.
    Guarantees strict concurrency safety with zero external Lua dependencies.
    """

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client

    async def check(self, key: str, policy: TierPolicy, cost: int = 1) -> RateLimitResult:
        """Evaluates rate limit against Redis with OTel telemetry."""
        start_time = time.monotonic()
        with tracer.start_as_current_span("edgeshield.redis_check") as span:
            span.set_attribute("rate_limit.key", key)
            span.set_attribute("rate_limit.tier", policy.name)
            span.set_attribute("rate_limit.algorithm", policy.algorithm)

            if policy.algorithm == "token_bucket":
                result = await self._check_token_bucket(key, policy, cost)
            else:
                result = await self._check_sliding_window(key, policy, cost)

            duration = time.monotonic() - start_time
            redis_latency_histogram.record(duration, {"algorithm": policy.algorithm, "tier": policy.name})
            span.set_attribute("rate_limit.allowed", result.allowed)
            span.set_attribute("rate_limit.remaining", result.remaining)
            return result

    async def _check_sliding_window(self, key: str, policy: TierPolicy, cost: int) -> RateLimitResult:
        """
        Pure Python Atomic Sliding Window Log via single-transaction Redis Pipeline.
        Atomically records the request, retrieves its strict monotonic rank,
        and enforces limits without check-then-act race conditions.
        """
        redis_key = f"edgeshield:sw:{key}"
        now_ms = time.time() * 1000.0
        window_ms = policy.window_seconds * 1000.0
        clear_before = now_ms - window_ms
        member = f"{now_ms}:{uuid.uuid4().hex}"

        # 1. Execute atomic pipeline: evict expired, add current request, get its rank & oldest entry
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, clear_before)
            pipe.zadd(redis_key, {member: now_ms})
            pipe.zrank(redis_key, member)
            pipe.pexpire(redis_key, int(window_ms))
            pipe.zrange(redis_key, 0, 0, withscores=True)
            res = await pipe.execute()

        _cleaned, _added, rank, _pexpire, oldest_entries = res

        # 2. Check if request position in sorted set is within the allowed limit
        if rank is not None and rank < policy.limit:
            remaining = max(0, policy.limit - (rank + 1))
            return RateLimitResult(
                allowed=True,
                limit=policy.limit,
                remaining=remaining,
                reset_after_seconds=0.0,
                tier=policy.name,
                is_fallback=False,
            )
        else:
            # Over quota: Roll back this speculative entry so it doesn't inflate future window counts
            await self.redis.zrem(redis_key, member)

            if oldest_entries and len(oldest_entries) > 0:
                oldest_ts = float(oldest_entries[0][1])
                retry_after_ms = (oldest_ts + window_ms) - now_ms
                reset_after = max(0.001, retry_after_ms / 1000.0)
            else:
                reset_after = float(policy.window_seconds)

            return RateLimitResult(
                allowed=False,
                limit=policy.limit,
                remaining=0,
                reset_after_seconds=reset_after,
                tier=policy.name,
                is_fallback=False,
            )

    async def _check_token_bucket(self, key: str, policy: TierPolicy, cost: int) -> RateLimitResult:
        """
        Pure Python Token Bucket with optimistic concurrency control (WATCH / MULTI / EXEC).
        """
        redis_key = f"edgeshield:tb:{key}"
        now_sec = time.time()
        capacity = float(policy.burst_capacity)
        refill_rate = float(policy.refill_rate)
        ttl_seconds = max(60, int((capacity / refill_rate) * 2))

        async with self.redis.pipeline() as pipe:
            for _ in range(5):  # Max 5 retry attempts on contention
                try:
                    await pipe.watch(redis_key)
                    raw = await pipe.hmget(redis_key, ["tokens", "last_updated"])
                    raw_tokens, raw_last_updated = raw[0], raw[1]

                    if raw_tokens is None or raw_last_updated is None:
                        current_tokens = capacity
                        last_updated = now_sec
                    else:
                        stored_tokens = float(raw_tokens)
                        last_updated = float(raw_last_updated)
                        elapsed = max(0.0, now_sec - last_updated)
                        current_tokens = min(capacity, stored_tokens + (elapsed * refill_rate))
                        last_updated = now_sec

                    pipe.multi()
                    if current_tokens >= cost:
                        current_tokens -= cost
                        pipe.hset(
                            redis_key,
                            mapping={"tokens": str(current_tokens), "last_updated": str(last_updated)},
                        )
                        pipe.expire(redis_key, ttl_seconds)
                        await pipe.execute()

                        return RateLimitResult(
                            allowed=True,
                            limit=policy.limit,
                            remaining=int(current_tokens),
                            reset_after_seconds=0.0,
                            tier=policy.name,
                            is_fallback=False,
                        )
                    else:
                        missing = cost - current_tokens
                        reset_after = max(0.001, missing / refill_rate)
                        pipe.hset(
                            redis_key,
                            mapping={"tokens": str(current_tokens), "last_updated": str(last_updated)},
                        )
                        pipe.expire(redis_key, ttl_seconds)
                        await pipe.execute()

                        return RateLimitResult(
                            allowed=False,
                            limit=policy.limit,
                            remaining=0,
                            reset_after_seconds=reset_after,
                            tier=policy.name,
                            is_fallback=False,
                        )
                except aioredis.WatchError:
                    continue

        # If contention exhausted retries, rate limit safely
        return RateLimitResult(
            allowed=False,
            limit=policy.limit,
            remaining=0,
            reset_after_seconds=1.0,
            tier=policy.name,
            is_fallback=False,
        )
