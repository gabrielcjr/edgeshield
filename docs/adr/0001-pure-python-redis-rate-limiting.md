# ADR 0001: Pure Python Distributed Rate Limiting via Async Redis Pipelines

## Status
Accepted

## Context
When running an API Gateway in horizontally scaled environments (e.g. multi-replica Kubernetes pods in K3s), local in-memory rate limiting alone cannot enforce aggregate quota policies across multiple gateway replicas. Multiple pods receiving concurrent requests from the same client must share a single distributed state store.

While Lua scripting inside Redis is an option, it introduces a split language maintenance burden, makes unit testing and mocking more complex (requiring Lua runtimes), and restricts developer ergonomics.

## Decision
We implement all distributed rate limiting algorithms exclusively in **Pure Python 3.12** using asynchronous Redis pipelines (`redis.asyncio.client.Pipeline`) and atomic multi-command batches with transactional guarantees (`WATCH` / `MULTI` / `EXEC`).

### Pure Python Implementations:

1. **Sliding Window Counter / Log (Pure Python Async Pipeline):**
   - Utilizes Redis Sorted Sets (`ZSET`) where scores are microsecond timestamps and members are unique UUIDs.
   - The Python async engine builds a transactional pipeline:
     1. Cleans expired entries: `pipe.zremrangebyscore(key, 0, clear_before)`.
     2. Counts active entries in the window: `pipe.zcard(key)`.
     3. Queries the oldest entry timestamp in the window: `pipe.zrange(key, 0, 0, withscores=True)`.
     4. Executes the read/clean phase in a single pipeline network round-trip.
     5. Evaluates quota compliance in Python:
        - If `current_count + cost <= limit`: executes a second atomic pipeline adding the new request entry (`pipe.zadd(key, {member: now_ms})`), setting window TTL (`pipe.pexpire(key, window_ms)`), and returns `allowed=True`.
        - If exceeded: calculates `retry_after_ms` from the oldest timestamp and returns `allowed=False`.

2. **Token Bucket (Pure Python Optimistic Concurrency with WATCH):**
   - Stores bucket state (`tokens`, `last_updated`) in a Redis Hash.
   - The Python engine uses `WATCH` and dynamic replenishment:
     `current_tokens = min(capacity, stored_tokens + (elapsed * refill_rate))`
   - Decrements tokens and commits via `MULTI`/`EXEC`, automatically retrying on transaction conflict.

## Consequences
### Positive
* **100% Python Codebase:** Eliminates external Lua dependencies; testable natively using `fakeredis` and standard Python mocking.
* **Maintainability & Typing:** Full type hints (`typing`, Pydantic models) across all rate limiting paths.
* **Observability:** Granular span tracking in Python for every pipeline phase and retry loop, directly exportable to SigNoz.

### Negative / Trade-offs
* Requires pipeline/transaction handling in Python; mitigated by asynchronous pipelining reducing network round-trip overhead.
