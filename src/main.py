"""Main Application Entry Point for EdgeShield Distributed Gateway."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from src.config import settings
from src.core.circuit_breaker import CircuitBreaker
from src.core.errors import UpstreamError
from src.limiters.hybrid_limiter import HybridRateLimiter
from src.limiters.memory_limiter import MemoryRateLimiter
from src.middleware.rate_limit_middleware import RateLimitMiddleware
from src.proxy.reverse_proxy import ReverseProxyEngine
from src.telemetry.otel import setup_telemetry

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
logger = logging.getLogger("edgeshield.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application lifecycle: Redis connection, OTel telemetry, and HTTP client."""
    logger.info("Initializing EdgeShield Gateway [Env: %s]", settings.app_env)

    # 1. Setup OpenTelemetry & SigNoz
    setup_telemetry(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        environment=settings.app_env,
        enabled=settings.otel_enabled,
    )

    # 2. Setup Redis Connection Pool
    redis_client = None
    try:
        redis_client = aioredis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_timeout_seconds,
            socket_connect_timeout=settings.redis_timeout_seconds,
            max_connections=settings.redis_max_connections,
            decode_responses=False,
        )
        # Verify connection
        await redis_client.ping()
        logger.info("Successfully connected to Redis distributed cache at %s", settings.redis_url)
    except Exception as exc:
        logger.warning("Could not establish immediate Redis connection (%s). Circuit breaker will handle fallback.", exc)

    # 3. Setup Circuit Breaker & Limiters
    circuit_breaker = CircuitBreaker(
        name="redis_gateway_guard",
        failure_threshold=settings.circuit_breaker_failure_threshold,
        recovery_timeout=settings.circuit_breaker_recovery_timeout,
        half_open_probes=settings.circuit_breaker_half_open_probes,
    )
    memory_limiter = MemoryRateLimiter(max_entries=settings.memory_fallback_max_entries)
    hybrid_limiter = HybridRateLimiter(
        redis_client=redis_client,
        circuit_breaker=circuit_breaker,
        memory_limiter=memory_limiter,
    )

    # 4. Setup Reverse Proxy
    proxy_engine = ReverseProxyEngine(
        upstream_base_url=settings.upstream_url,
        timeout_seconds=settings.upstream_timeout_seconds,
    )

    app.state.redis_client = redis_client
    app.state.circuit_breaker = circuit_breaker
    app.state.hybrid_limiter = hybrid_limiter
    app.state.memory_limiter = memory_limiter
    app.state.proxy_engine = proxy_engine

    yield

    # Cleanup on shutdown
    logger.info("Shutting down EdgeShield Gateway resources...")
    if redis_client:
        await redis_client.aclose()
    await proxy_engine.close()


def create_app() -> FastAPI:
    """Application factory for EdgeShield."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="High-Performance Distributed API Gateway & Rate-Limiter with SigNoz Observability",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Setup temporary limiter for middleware attachment (replaced in lifespan)
    fallback_limiter = MemoryRateLimiter()
    app.add_middleware(RateLimitMiddleware, limiter=fallback_limiter, settings=settings)

    @app.exception_handler(UpstreamError)
    async def upstream_error_handler(request: Request, exc: UpstreamError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"type": "https://edgeshield.internal/errors/upstream", "title": "Upstream Gateway Error", "detail": exc.detail},
        )

    @app.get("/healthz", tags=["Operations"])
    async def health_check():
        """Health check endpoint for Kubernetes liveness & readiness probes."""
        cb = getattr(app.state, "circuit_breaker", None)
        cb_state = cb.state.value if cb else "UNKNOWN"
        return {
            "status": "healthy",
            "service": settings.app_name,
            "circuit_breaker": cb_state,
            "signoz_endpoint": settings.otel_exporter_otlp_endpoint,
        }

    @app.get("/api/v1/status", tags=["Operations"])
    async def gateway_status():
        """Returns active rate-limiting tiers and operational telemetry status."""
        cb = getattr(app.state, "circuit_breaker", None)
        return {
            "gateway": settings.app_name,
            "circuit_breaker_state": cb.state.value if cb else "UNKNOWN",
            "tiers": {k: v.model_dump() for k, v in settings.tiers.items()},
            "signoz_telemetry_enabled": settings.otel_enabled,
        }

    @app.get("/api/mock/ping", tags=["Mock"])
    async def mock_ping():
        """Built-in test endpoint for benchmark and latency evaluation."""
        return {"message": "pong", "gateway": "EdgeShield"}

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], tags=["Proxy"])
    async def catch_all_proxy(request: Request, full_path: str):
        """Dispatches all incoming application routes to the upstream target."""
        proxy_engine: ReverseProxyEngine = getattr(app.state, "proxy_engine", None)
        if not proxy_engine:
            return JSONResponse(status_code=503, content={"detail": "Proxy engine not initialized"})
        return await proxy_engine.proxy(request, target_path=f"/{full_path}")

    return app


app = create_app()
