"""Pure Python In-Memory Rate Limiter with Local Sliding Window and Token Bucket."""

import asyncio
import collections
import time
from typing import Deque, Dict, Tuple
from src.config import TierPolicy
from src.core.policies import RateLimitResult
from src.limiters.base import BaseRateLimiter


class MemoryRateLimiter(BaseRateLimiter):
    """
    High-performance in-memory Rate Limiter acting as a localized fallback
    or standalone limiter with bounded memory.
    """

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        # Key -> Deque of millisecond timestamps (for sliding window)
        self._sliding_windows: Dict[str, Deque[float]] = {}
        # Key -> (tokens: float, last_updated: float) (for token bucket)
        self._token_buckets: Dict[str, Tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, policy: TierPolicy, cost: int = 1) -> RateLimitResult:
        """Evaluates in-memory rate limit."""
        async with self._lock:
            if policy.algorithm == "token_bucket":
                return self._check_token_bucket(key, policy, cost)
            return self._check_sliding_window(key, policy, cost)

    def _check_sliding_window(self, key: str, policy: TierPolicy, cost: int) -> RateLimitResult:
        now_ms = time.time() * 1000.0
        window_ms = policy.window_seconds * 1000.0
        clear_before = now_ms - window_ms

        # LRU cleanup if max entries exceeded
        if len(self._sliding_windows) > self.max_entries and key not in self._sliding_windows:
            oldest_key = next(iter(self._sliding_windows))
            del self._sliding_windows[oldest_key]

        if key not in self._sliding_windows:
            self._sliding_windows[key] = collections.deque()

        timestamps = self._sliding_windows[key]

        # Evict timestamps outside the active window
        while timestamps and timestamps[0] <= clear_before:
            timestamps.popleft()

        current_count = len(timestamps)

        if current_count + cost <= policy.limit:
            for _ in range(cost):
                timestamps.append(now_ms)
            remaining = policy.limit - current_count - cost
            return RateLimitResult(
                allowed=True,
                limit=policy.limit,
                remaining=remaining,
                reset_after_seconds=0.0,
                tier=policy.name,
                is_fallback=True,
            )
        else:
            oldest_ts = timestamps[0] if timestamps else now_ms
            retry_after_ms = (oldest_ts + window_ms) - now_ms
            reset_after = max(0.001, retry_after_ms / 1000.0)
            return RateLimitResult(
                allowed=False,
                limit=policy.limit,
                remaining=0,
                reset_after_seconds=reset_after,
                tier=policy.name,
                is_fallback=True,
            )

    def _check_token_bucket(self, key: str, policy: TierPolicy, cost: int) -> RateLimitResult:
        now_sec = time.time()
        capacity = float(policy.burst_capacity)
        refill_rate = float(policy.refill_rate)

        if key not in self._token_buckets:
            tokens = capacity
            last_updated = now_sec
        else:
            stored_tokens, last_updated = self._token_buckets[key]
            elapsed = max(0.0, now_sec - last_updated)
            tokens = min(capacity, stored_tokens + (elapsed * refill_rate))
            last_updated = now_sec

        if tokens >= cost:
            tokens -= cost
            self._token_buckets[key] = (tokens, last_updated)
            return RateLimitResult(
                allowed=True,
                limit=policy.limit,
                remaining=int(tokens),
                reset_after_seconds=0.0,
                tier=policy.name,
                is_fallback=True,
            )
        else:
            self._token_buckets[key] = (tokens, last_updated)
            missing = cost - tokens
            reset_after = max(0.001, missing / refill_rate)
            return RateLimitResult(
                allowed=False,
                limit=policy.limit,
                remaining=0,
                reset_after_seconds=reset_after,
                tier=policy.name,
                is_fallback=True,
            )

    def reset(self) -> None:
        """Clears all in-memory tracking structures (useful for tests)."""
        self._sliding_windows.clear()
        self._token_buckets.clear()
