"""Configuration settings for EdgeShield API Gateway."""

from typing import Dict
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TierPolicy(BaseModel):
    """Rate limit configuration for an authorization tier."""
    name: str
    limit: int = 100                 # max requests per window
    window_seconds: int = 60         # sliding window duration
    burst_capacity: int = 20         # token bucket burst
    refill_rate: float = 5.0         # tokens per second
    algorithm: str = "sliding_window" # "sliding_window" or "token_bucket"


class Settings(BaseSettings):
    """Global Gateway & Rate Limiter settings loaded from environment."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Gateway Server
    app_name: str = "EdgeShield Distributed Gateway"
    app_env: str = "production"
    port: int = 8080
    host: str = "0.0.0.0"
    debug: bool = False

    # Upstream Target Service (default mock or dev service)
    upstream_url: str = Field(default="http://localhost:8000")
    upstream_timeout_seconds: float = Field(default=30.0)

    # Redis Distributed Cache
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_timeout_seconds: float = Field(default=0.5)
    redis_max_connections: int = Field(default=50)

    # Circuit Breaker & Fallback
    circuit_breaker_failure_threshold: int = Field(default=5)
    circuit_breaker_recovery_timeout: float = Field(default=15.0)
    circuit_breaker_half_open_probes: int = Field(default=3)
    memory_fallback_max_entries: int = Field(default=10000)

    # SigNoz / OpenTelemetry
    otel_exporter_otlp_endpoint: str = Field(
        default="http://signoz-otel-collector.signoz.svc.cluster.local:4318"
    )
    otel_service_name: str = Field(default="edgeshield-gateway")
    otel_enabled: bool = Field(default=True)

    # Defined Tier Policies
    tiers: Dict[str, TierPolicy] = {
        "anonymous": TierPolicy(name="anonymous", limit=20, window_seconds=60, burst_capacity=5, refill_rate=0.5),
        "free": TierPolicy(name="free", limit=60, window_seconds=60, burst_capacity=15, refill_rate=1.0),
        "pro": TierPolicy(name="pro", limit=300, window_seconds=60, burst_capacity=50, refill_rate=5.0),
        "enterprise": TierPolicy(name="enterprise", limit=2000, window_seconds=60, burst_capacity=200, refill_rate=35.0),
    }


settings = Settings()
