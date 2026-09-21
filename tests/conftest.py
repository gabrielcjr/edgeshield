"""Pytest configuration and test fixtures for EdgeShield."""

import asyncio
import pytest
import pytest_asyncio
import fakeredis.aioredis as fake_aioredis
from httpx import AsyncClient, ASGITransport

from src.config import Settings, TierPolicy
from src.core.circuit_breaker import CircuitBreaker
from src.limiters.hybrid_limiter import HybridRateLimiter
from src.limiters.memory_limiter import MemoryRateLimiter
from src.limiters.redis_limiter import RedisRateLimiter
from src.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def fake_redis():
    """Provides a fresh in-memory FakeRedis async client for testing."""
    client = fake_aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def sample_sliding_policy():
    """Policy allowing 5 requests per 10-second window."""
    return TierPolicy(
        name="test_sliding",
        limit=5,
        window_seconds=10,
        algorithm="sliding_window",
    )


@pytest.fixture
def sample_token_policy():
    """Policy allowing burst of 3 tokens with refill rate 1 token/sec."""
    return TierPolicy(
        name="test_token",
        limit=10,
        window_seconds=10,
        burst_capacity=3,
        refill_rate=1.0,
        algorithm="token_bucket",
    )


@pytest_asyncio.fixture
async def test_app(fake_redis):
    """Initializes a FastAPI test application wired to FakeRedis and local limiters."""
    app = create_app()

    circuit_breaker = CircuitBreaker(
        name="test_cb",
        failure_threshold=3,
        recovery_timeout=0.2,
        half_open_probes=2,
    )
    memory_limiter = MemoryRateLimiter()
    hybrid_limiter = HybridRateLimiter(
        redis_client=fake_redis,
        circuit_breaker=circuit_breaker,
        memory_limiter=memory_limiter,
    )

    app.state.redis_client = fake_redis
    app.state.circuit_breaker = circuit_breaker
    app.state.hybrid_limiter = hybrid_limiter
    app.state.memory_limiter = memory_limiter

    return app


@pytest_asyncio.fixture
async def async_client(test_app):
    """Provides an AsyncClient for end-to-end API testing."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
