"""Hybrid Rate Limiter pairing Distributed Redis with Circuit Breaker and In-Memory Fallback."""

import logging
from typing import Optional
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from src.config import TierPolicy
from src.core.circuit_breaker import CircuitBreaker, CircuitState
from src.core.errors import CircuitBreakerOpenError
from src.core.policies import RateLimitResult
from src.limiters.base import BaseRateLimiter
from src.limiters.memory_limiter import MemoryRateLimiter
from src.limiters.redis_limiter import RedisRateLimiter
from src.telemetry.otel import fallback_counter, tracer

logger = logging.getLogger("edgeshield.hybrid_limiter")


class HybridRateLimiter(BaseRateLimiter):
    """
    Dual-Tier resilient limiter:
      Primary: RedisRateLimiter (Distributed, Pure Python)
      Protection: CircuitBreaker (Fast-fail when Redis fails)
      Fallback: MemoryRateLimiter (In-Memory sliding window / token bucket)
    """

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        memory_limiter: Optional[MemoryRateLimiter] = None,
    ):
        self.redis_client = redis_client
        self.redis_limiter = RedisRateLimiter(redis_client) if redis_client else None
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name="redis_guard")
        self.memory_limiter = memory_limiter or MemoryRateLimiter()

    async def check(self, key: str, policy: TierPolicy, cost: int = 1) -> RateLimitResult:
        """
        Attempts distributed rate limiting via Redis.
        Gracefully falls back to local memory if Redis is down or circuit is OPEN.
        """
        if not self.redis_limiter:
            return await self._fallback(key, policy, cost, reason="no_redis_configured")

        try:
            # Execute through Circuit Breaker
            result = await self.circuit_breaker.call(
                self.redis_limiter.check, key, policy, cost
            )
            return result

        except (CircuitBreakerOpenError, ConnectionError, TimeoutError, RedisError) as exc:
            reason = type(exc).__name__
            logger.warning(
                "Redis rate-limit evaluation failed (%s). Falling back to in-memory limiter. CB State: %s",
                reason,
                self.circuit_breaker.state.value,
            )
            return await self._fallback(key, policy, cost, reason=reason)

    async def _fallback(self, key: str, policy: TierPolicy, cost: int, reason: str) -> RateLimitResult:
        """Executes in-memory fallback evaluation and instruments telemetry."""
        with tracer.start_as_current_span("edgeshield.fallback_evaluation") as span:
            span.set_attribute("fallback.reason", reason)
            span.set_attribute("circuit_breaker.state", self.circuit_breaker.state.value)

            fallback_counter.add(1, {"tier": policy.name, "reason": reason})

            result = await self.memory_limiter.check(key, policy, cost)
            result.is_fallback = True
            return result
