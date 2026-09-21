"""Starlette / ASGI Rate Limiting Middleware with RFC 7807 Error Responses."""

import time
from typing import Callable, Optional, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config import Settings
from src.core.policies import ClientIdentityExtractor
from src.limiters.base import BaseRateLimiter
from src.telemetry.otel import record_request_metric, tracer

# Endpoints and static asset extensions excluded from rate limiting
EXCLUDED_PATHS: Set[str] = {"/healthz", "/health", "/metrics", "/favicon.ico", "/docs", "/openapi.json"}
STATIC_EXTENSIONS: Set[str] = {
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map"
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware intercepting incoming HTTP requests to enforce rate limits,
    inject standard headers, and return RFC 7807 compliant 429 responses.
    """

    def __init__(self, app, limiter: BaseRateLimiter, settings: Settings):
        super().__init__(app)
        self.limiter = limiter
        self.settings = settings

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Bypass rate limiting for internal health / metrics endpoints and static web assets
        if path in EXCLUDED_PATHS or any(path.lower().endswith(ext) for ext in STATIC_EXTENSIONS):
            return await call_next(request)

        # 1. Identify Client and Authorization Tier
        key, tier_name = ClientIdentityExtractor.extract_identity(request)
        policy = self.settings.tiers.get(tier_name, self.settings.tiers.get("anonymous"))

        with tracer.start_as_current_span(f"edgeshield.rate_limit_policy {tier_name}") as span:
            span.set_attribute("client.key", key)
            span.set_attribute("client.tier", tier_name)

            # 2. Evaluate Rate Limit using active app limiter if configured
            active_limiter = getattr(request.app.state, "hybrid_limiter", self.limiter)
            result = await active_limiter.check(key=key, policy=policy, cost=1)

            # 3. Handle Rate Limit Exceeded (HTTP 429)
            if not result.allowed:
                record_request_metric(tier=tier_name, status_code=429, is_blocked=True, is_fallback=result.is_fallback)

                problem_details = {
                    "type": "https://edgeshield.internal/errors/rate-limit-exceeded",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": f"Rate limit exceeded for tier '{tier_name}'. Retry in {result.reset_after_seconds:.2f} seconds.",
                    "tier": tier_name,
                    "limit": result.limit,
                    "retry_after_seconds": round(result.reset_after_seconds, 3),
                }

                headers = {
                    "Retry-After": str(result.retry_after_int),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": f"{result.reset_after_seconds:.3f}",
                    "X-RateLimit-Tier": tier_name,
                    "Content-Type": "application/problem+json",
                }
                if result.is_fallback:
                    headers["X-EdgeShield-Fallback"] = "1"

                return JSONResponse(status_code=429, content=problem_details, headers=headers)

            # 4. Request Allowed: Proceed to handler or upstream proxy
            response = await call_next(request)

            # 5. Inject Standard Rate Limit Headers
            response.headers["X-RateLimit-Limit"] = str(result.limit)
            response.headers["X-RateLimit-Remaining"] = str(result.remaining)
            response.headers["X-RateLimit-Reset"] = f"{result.reset_after_seconds:.3f}"
            response.headers["X-RateLimit-Tier"] = tier_name
            if result.is_fallback:
                response.headers["X-EdgeShield-Fallback"] = "1"

            record_request_metric(tier=tier_name, status_code=response.status_code, is_blocked=False, is_fallback=result.is_fallback)
            return response
