"""Custom exceptions for EdgeShield Gateway & Rate Limiter."""

from typing import Optional


class EdgeShieldError(Exception):
    """Base exception for all EdgeShield errors."""
    pass


class RateLimitExceeded(EdgeShieldError):
    """Raised when an incoming request exceeds the configured quota."""

    def __init__(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        retry_after_seconds: float,
        tier: str = "default",
        message: Optional[str] = None,
    ):
        self.key = key
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after_seconds = max(0.001, retry_after_seconds)
        self.tier = tier
        super().__init__(
            message or f"Rate limit exceeded for tier '{tier}'. Try again in {self.retry_after_seconds:.2f}s."
        )


class CircuitBreakerOpenError(EdgeShieldError):
    """Raised when Redis calls are rejected because the Circuit Breaker is OPEN."""

    def __init__(self, message: str = "Circuit breaker is OPEN. Fast-failing to in-memory fallback."):
        super().__init__(message)


class UpstreamError(EdgeShieldError):
    """Raised when an upstream service call fails or times out."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Upstream error ({status_code}): {detail}")
