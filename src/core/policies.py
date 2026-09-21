"""Models and extractors for Rate Limiting Policies and Client Identities."""

from dataclasses import dataclass
from typing import Optional
from starlette.requests import Request


@dataclass(slots=True)
class RateLimitResult:
    """The outcome of evaluating a rate limit policy."""
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: float
    tier: str
    is_fallback: bool = False

    @property
    def retry_after_int(self) -> int:
        """Integer ceiling of retry_after for the HTTP Retry-After header."""
        return max(1, int(self.reset_after_seconds + 0.999))


class ClientIdentityExtractor:
    """Extracts client identity, rate-limiting key, and tier from incoming ASGI request."""

    @staticmethod
    def extract_client_ip(request: Request) -> str:
        """Extracts the true client IP from standard proxy headers."""
        # 1. Cloudflare header
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()

        # 2. X-Forwarded-For (leftmost is original client)
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            parts = [p.strip() for p in x_forwarded_for.split(",")]
            if parts and parts[0]:
                return parts[0]

        # 3. Direct client host
        if request.client and request.client.host:
            return request.client.host

        return "127.0.0.1"

    @classmethod
    def extract_identity(cls, request: Request) -> tuple[str, str]:
        """
        Determines the unique rate limit bucket key and tier.
        Returns: (bucket_key, tier_name)
        """
        # Check API Key header
        api_key = request.headers.get("x-api-key")
        if api_key:
            # Map key prefixes or values to tiers
            if api_key.startswith("ent_"):
                return f"key:{api_key}", "enterprise"
            elif api_key.startswith("pro_"):
                return f"key:{api_key}", "pro"
            else:
                return f"key:{api_key}", "free"

        # Check Authorization Bearer
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token.startswith("pro_"):
                return f"jwt:{token[:16]}", "pro"
            return f"jwt:{token[:16]}", "free"

        # Fallback to Client IP
        ip = cls.extract_client_ip(request)
        return f"ip:{ip}", "anonymous"
