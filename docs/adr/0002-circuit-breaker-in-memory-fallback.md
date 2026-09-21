# ADR 0002: In-Memory Circuit Breaker Fallback & Graceful Degradation

## Status
Accepted

## Context
In a distributed architecture, external dependencies like Redis can experience transient network partitions, CPU saturation, or complete node failure. If the API Gateway synchronously depends on Redis to evaluate every request, any Redis outage or elevated latency would directly cascade, causing all incoming API requests to either fail (failing closed) or stall the gateway (thread pool / event loop exhaustion).

Conversely, failing completely open without any rate limiting exposes upstream services (DevATS, AMAE, etc.) to unconstrained traffic surges and potential cascading failure.

## Decision
We implement a **Dual-Tier Resilient Architecture** pairing a distributed rate limiter with an in-process **Circuit Breaker** and an **In-Memory Local Fallback Limiter**.

### Circuit Breaker States:
1. **CLOSED:** Normal operation. All rate limit checks are routed to Redis. Failure counters reset upon consecutive successes.
2. **OPEN:** When the consecutive failure threshold (e.g. 5 timeouts/connection errors) or error rate exceeds the trigger threshold within a sliding window, the circuit trips to OPEN. All Redis calls are bypassed immediately without blocking. Requests are routed to the **In-Memory Fallback Limiter**.
3. **HALF-OPEN:** After a recovery timeout (e.g. 15 seconds), the circuit enters HALF-OPEN. A limited percentage of probe requests are dispatched to Redis. If the probes succeed, the circuit resets to CLOSED. If any probe fails, the circuit re-trips to OPEN.

### In-Memory Fallback Limiter:
* Uses a high-throughput, thread-safe in-memory sliding window / token bucket implemented in Python.
* Maintains a bounded local cache with LRU eviction to prevent memory bloat on gateway instances.
* Ensures upstream services remain protected by enforcing localized rate limits even while the distributed cache is offline.

## Consequences
### Positive
* **Zero Cascading Downtime:** Gateway availability remains high even when Redis crashes or network degrades.
* **Predictable Latency:** Requests fail fast to in-memory fallback (<0.1ms) instead of hanging on Redis connection timeouts.
* **Continuous Protection:** Upstream services are not flooded during Redis outages.

### Negative / Trade-offs
* During an outage, rate limits are enforced per-pod rather than globally aggregated, which temporarily allows slightly higher aggregate traffic across multiple replicas. This is an intentional CAP-theorem trade-off (choosing Availability over strict Consistency during partition).
