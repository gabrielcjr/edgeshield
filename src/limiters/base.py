"""Abstract Base Rate Limiter Interface."""

from abc import ABC, abstractmethod
from src.config import TierPolicy
from src.core.policies import RateLimitResult


class BaseRateLimiter(ABC):
    """Abstract base class for rate limit engines."""

    @abstractmethod
    async def check(self, key: str, policy: TierPolicy, cost: int = 1) -> RateLimitResult:
        """
        Evaluates whether a request with a given cost is allowed under policy.
        Returns RateLimitResult.
        """
        pass
