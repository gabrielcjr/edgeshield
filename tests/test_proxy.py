"""End-to-End API and Gateway Integration Tests."""

import pytest


@pytest.mark.asyncio
async def test_healthz_endpoint(async_client):
    response = await async_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "circuit_breaker" in data


@pytest.mark.asyncio
async def test_status_endpoint(async_client):
    response = await async_client.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert "tiers" in data
    assert "anonymous" in data["tiers"]
    assert "enterprise" in data["tiers"]


@pytest.mark.asyncio
async def test_rate_limit_headers_injected_on_success(async_client):
    response = await async_client.get("/api/mock/ping", headers={"x-api-key": "pro_test_key_123"})
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Tier"] == "pro"
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Remaining" in response.headers
    assert "X-RateLimit-Reset" in response.headers


@pytest.mark.asyncio
async def test_rfc_7807_problem_details_when_rate_limit_exceeded(async_client, test_app):
    # Set tight quota for anonymous tier: 2 requests
    test_app.state.hybrid_limiter.memory_limiter.reset()

    # Make requests with specific IP
    headers = {"x-forwarded-for": "198.51.100.42"}

    # Exhaust quota (anonymous default in config is 20, let's exhaust or test with custom tier)
    responses = []
    for _ in range(25):
        res = await async_client.get("/api/mock/ping", headers=headers)
        responses.append(res)

    # At least the later requests must be 429
    last_res = responses[-1]
    assert last_res.status_code == 429
    assert last_res.headers["content-type"] == "application/problem+json"
    assert "Retry-After" in last_res.headers
    assert last_res.headers["X-RateLimit-Remaining"] == "0"

    body = last_res.json()
    assert body["status"] == 429
    assert body["type"] == "https://edgeshield.internal/errors/rate-limit-exceeded"
    assert "retry_after_seconds" in body
