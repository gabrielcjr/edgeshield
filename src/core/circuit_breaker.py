"""Pure Python 3-State Circuit Breaker with Telemetry Hooks."""

import asyncio
import enum
import time
from typing import Any, Callable, Coroutine, Optional
from src.core.errors import CircuitBreakerOpenError


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Asynchronous Circuit Breaker protecting against cascading Redis or upstream failures.
    Transitions:
      CLOSED -> (failures >= threshold) -> OPEN
      OPEN -> (after recovery_timeout) -> HALF_OPEN
      HALF_OPEN -> (probes succeed) -> CLOSED
      HALF_OPEN -> (probe fails) -> OPEN
    """

    def __init__(
        self,
        name: str = "redis_circuit_breaker",
        failure_threshold: int = 5,
        recovery_timeout: float = 15.0,
        half_open_probes: int = 3,
        on_state_change: Optional[Callable[[CircuitState], None]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_probes = half_open_probes
        self.on_state_change = on_state_change

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._successful_probes = 0
        self._last_state_change = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Returns the current state, evaluating automatic recovery to HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transitions to a new state and triggers callback."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.monotonic()
        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._successful_probes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._successful_probes = 0

        if old_state != new_state and self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception:
                pass

    async def record_success(self) -> None:
        """Records a successful operation."""
        async with self._lock:
            current = self.state
            if current == CircuitState.HALF_OPEN:
                self._successful_probes += 1
                if self._successful_probes >= self.half_open_probes:
                    self._transition_to(CircuitState.CLOSED)
            elif current == CircuitState.CLOSED:
                self._failure_count = 0

    async def record_failure(self, error: Optional[Exception] = None) -> None:
        """Records an operation failure."""
        async with self._lock:
            current = self.state
            if current == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN immediately re-trips the circuit
                self._transition_to(CircuitState.OPEN)
            elif current == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Executes an asynchronous function guarded by the circuit breaker.
        Raises CircuitBreakerOpenError immediately if OPEN.
        """
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN. Fast-failing."
            )

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as exc:
            await self.record_failure(exc)
            raise exc
