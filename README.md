# EdgeShield — High-Performance Distributed API Gateway & Rate-Limiter Engine

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/Redis-Distributed%20Pipelines-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-SigNoz%20Native-F5A800.svg?logo=opentelemetry&logoColor=white)](https://signoz.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**EdgeShield** is a production-grade, distributed API Gateway and Rate-Limiting engine built in **Pure Python 3.12** using `FastAPI`, `AsyncIO`, and asynchronous Redis pipelines. Engineered for high-throughput microservice ecosystems, it protects upstream services against traffic spikes, noisy neighbors, and brute-force bursts while exporting native distributed traces and metrics to **SigNoz**.

---

## Architecture

```
                          [ Client Request Traffic ]
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      EdgeShield (Python Async Gateway)                      │
│                                                                             │
│  ┌─────────────────────────┐     ┌───────────────────────────────────────┐  │
│  │  Policy Evaluator       │ ──> │  Tier & Route Matcher                 │  │
│  │  (IP / API Key / Tier)  │     │  (Anonymous / Free / Pro / Enterprise)│  │
│  └─────────────────────────┘     └───────────────────────────────────────┘  │
│                   │                                                         │
│                   ▼                                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Rate Limiting Engine                               │  │
│  │                                                                       │  │
│  │  [ Redis Distributed Pipeline ] ── (Failure) ──> [ Circuit Breaker ]  │  │
│  │   • Atomic Sliding Window                           │                 │  │
│  │   • Monotonic Rank Check                            ▼                 │  │
│  │   • Pure Python Async              [ Local In-Memory Fallback Cache ] │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                   │                                                         │
│         Allowed   │ Rejection (HTTP 429 Too Many Requests)                  │
│                   ▼                                                         │
│  ┌─────────────────────────┐     ┌───────────────────────────────────────┐  │
│  │  Reverse Proxy Engine   │     │  RFC 7807 Problem Details             │  │
│  │  (HTTPX Streaming)      │     │  X-RateLimit-Limit, Remaining, Reset  │  │
│  └─────────────────────────┘     └───────────────────────────────────────┘  │
│                   │                                                         │
│                   ▼                                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │          OpenTelemetry Instrumentation (OTel SDK)                     │  │
│  │  • W3C TraceContext traceparent injection                             │  │
│  │  • Metrics: requests_total, ratelimit_blocked, redis_latency          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└───────────────────────┬──────────────────────────────────┬──────────────────┘
                        │                                  │
      Reverse Proxy     ▼                OTLP Spans/       ▼
┌───────────────────────────────┐        Metrics   ┌──────────────────────────┐
│  Upstream Microservices       │                  │  SigNoz OTel Collector   │
│  (DevATS / AMAE / etc.)       │                  │  (:4318 OTLP / Traces)   │
└───────────────────────────────┘                  └──────────────────────────┘
```

---

## Key Highlights & Architectural Strengths

1. **100% Pure Python & Zero External Lua Scripts:**
   - Evaluates sliding windows atomically using Redis Sorted Sets (`ZSET`) and pipelines in a single transaction round-trip, utilizing monotonic `ZRANK` guarantees to eliminate *check-then-act* concurrency bugs under high load.
2. **Dual-Tier Resilient Fallback (In-Memory Circuit Breaker):**
   - If the Redis cluster experiences network partitions, high latency, or crashes, the in-process Circuit Breaker fast-fails to an in-memory sliding window cache, preserving service availability and shielding upstream services from being flooded.
3. **End-to-End SigNoz Observability (OpenTelemetry Native):**
   - Automatically exports distributed spans and custom metrics (`edgeshield_requests_total`, `edgeshield_ratelimit_blocked_total`, `edgeshield_redis_latency_seconds`) to the cluster's **SigNoz OTel Collector** (`:4318`), injecting W3C `traceparent` headers into upstream requests for full distributed flamegraphs.
4. **RFC 7807 & Standard HTTP Rate-Limit Headers:**
   - Responds with `application/problem+json` payload accompanied by standard headers: `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `X-RateLimit-Tier`.
5. **Spec-Driven Development & Architecture Decision Records:**
   - Fully documented with ADRs:
     - [ADR 0001: Pure Python Distributed Rate Limiting](docs/adr/0001-pure-python-redis-rate-limiting.md)
     - [ADR 0002: In-Memory Circuit Breaker Fallback](docs/adr/0002-circuit-breaker-in-memory-fallback.md)
     - [ADR 0003: OpenTelemetry & Native SigNoz Integration](docs/adr/0003-signoz-opentelemetry-telemetry.md)

---

## Authorization Tiers & Quota Policies

| Tier | Rate Limit | Window | Burst Capacity | Refill Rate | Identification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Anonymous** | 20 req | 60s | 5 tokens | 0.5/s | Client IP (`CF-Connecting-IP` / `X-Forwarded-For`) |
| **Free** | 60 req | 60s | 15 tokens | 1.0/s | Standard API Key / Bearer token |
| **Pro** | 300 req | 60s | 50 tokens | 5.0/s | API Key starting with `pro_` |
| **Enterprise** | 2,000 req | 60s | 200 tokens | 35.0/s | API Key starting with `ent_` |

---

## Running Locally

### 1. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite
```bash
pytest --cov=src --cov-report=term-missing
```

### 3. Start Gateway Server
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Kubernetes & K3s GitOps Deployment

EdgeShield can be deployed as an edge gateway or sidecar in front of any service in the cluster:

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Environment variables configured for SigNoz:
* `OTEL_EXPORTER_OTLP_ENDPOINT`: `http://signoz-otel-collector.signoz.svc.cluster.local:4318`
* `OTEL_SERVICE_NAME`: `edgeshield-gateway`
