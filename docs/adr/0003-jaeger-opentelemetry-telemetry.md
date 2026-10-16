# ADR 0003: OpenTelemetry Instrumentation & Native Jaeger Integration

## Status
Accepted

## Context
Production gateways require granular, real-time observability across three fundamental pillars: Tracing, Metrics, and Structured Logging. 

While legacy setups rely on scraping Prometheus endpoints and separate logging agents, our Kubernetes cluster utilizes **Jaeger** and OpenTelemetry as the unified distributed tracing and observability platform. Jaeger provides high-performance distributed trace analytics, span metrics, and call graphs via standard OTLP (OpenTelemetry Protocol).

## Decision
We instrument EdgeShield natively with the **OpenTelemetry Python SDK**, configured to push telemetry via OTLP directly to the cluster's Jaeger OTel Collector at `http://jaeger.jaeger.svc.cluster.local:4318`.

### Core Capabilities:
1. **W3C Distributed TraceContext Propagation:**
   - Every incoming request extracts incoming `traceparent` headers (or generates a new trace ID).
   - Injects the `traceparent` header into the upstream reverse proxy request dispatched to backend microservices (e.g. DevATS, AMAE).
   - Results in seamless end-to-end distributed flamegraphs in the Jaeger UI showing: `EdgeShield Gateway` ➔ `RateLimit Check` ➔ `Reverse Proxy Hop` ➔ `Upstream Service DB Query`.

2. **Custom OpenTelemetry Metrics:**
   - `edgeshield_requests_total`: Counter tracking all processed requests (dimensions: `tier`, `route`, `status_code`).
   - `edgeshield_ratelimit_blocked_total`: Counter tracking rejected requests (dimensions: `rule`, `client_id`, `tier`).
   - `edgeshield_redis_latency_seconds`: Histogram measuring execution duration of Redis Lua scripts.
   - `edgeshield_circuit_breaker_state`: Gauge indicating circuit breaker state (0 = Closed, 1 = Half-Open, 2 = Open).
   - `edgeshield_upstream_latency_seconds`: Histogram measuring upstream response times.

3. **Span Processors & Batching:**
   - Uses `BatchSpanProcessor` with non-blocking worker threads to ensure telemetry never introduces latency into the hot request-response path.
   - Standard semantic conventions applied to all HTTP gateway spans.

## Consequences
### Positive
* **Vendor-Agnostic Open Standard:** Uses 100% standard OpenTelemetry APIs without proprietary vendor locks.
* **Native Jaeger Visualization:** Traces are immediately indexable and queryable inside the Jaeger UI with low-latency trace search and service dependency graphs.
* **Correlated Telemetry:** Trace IDs are injected into structured logs and response headers (`X-Trace-Id`) for instant debugging.
