"""Retry backoff, cooperative cancellation, circuit breaker — ERROR_HANDLING.md §2-4,
SEQUENCE_DIAGRAMS.md §2-3.

Retries themselves use Celery's native `autoretry_for` / `self.retry(countdown=...)`
(ARCHITECTURE.md §2) — this module only computes the backoff schedule and
answers "has any token already reached the client," the gate that decides
whether a failure is retryable at all (ERROR_HANDLING.md §3).
"""

import time
from dataclasses import dataclass

from redis.client import Redis as SyncRedis

from shared.circuit import CIRCUIT_KEY, CircuitState


def compute_backoff_seconds(attempt_number: int, *, base_seconds: float) -> float:
    """attempt_number is 1-indexed (the retry about to be made). base=0.5 -> 0.5, 1, 2, ..."""
    return base_seconds * (2 ** (attempt_number - 1))


def cancel_key(request_id: str) -> str:
    return f"cancel:{request_id}"


class CancellationChecker:
    """Sync Redis GET wrapped as the `Callable[[], bool]` the `ModelProvider` protocol requires
    (ARCHITECTURE.md §4) — the provider's async loop already yields control per-token, so this
    stays a cheap synchronous poll rather than a new concurrency primitive (SEQUENCE_DIAGRAMS.md
    §2)."""

    def __init__(self, redis_client: SyncRedis, request_id: str) -> None:
        self._redis = redis_client
        self._key = cancel_key(request_id)

    def __call__(self) -> bool:
        return self._redis.get(self._key) is not None


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    state: CircuitState


class CircuitBreaker:
    """Per-worker in-memory decisions, backed by the shared `circuit:inference` Redis hash
    (DATA_MODEL.md §2) so every worker replica converges on the same view of "is the model
    healthy" without each one independently rediscovering an outage — ERROR_HANDLING.md §4.

    DATA_MODEL.md documents the hash as `(state, failureCount, openedAt)`. A fourth field,
    `windowStart`, is added here — undocumented there, but required to make "N failures within a
    sliding window" a real sliding window instead of an all-time counter that never resets. This
    is a disclosed extension of the documented schema, not a deviation from its intent.

    Known simplification: half-open's "exactly one probe" is approximate under multiple
    concurrent worker replicas (no distributed lock guards the transition) — acceptable for this
    project's single-worker-process default; a real multi-replica deployment would want a
    `SET NX`-guarded admission gate here instead.
    """

    def __init__(
        self,
        redis_client: SyncRedis,
        *,
        failure_threshold: int,
        window_seconds: int,
        cooldown_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        self._cooldown_seconds = cooldown_seconds

    def _read(self) -> dict[str, str]:
        # redis-py's stubs type hgetall() as `Awaitable[dict] | dict` regardless of sync/async
        # client (same stub quirk as app/services/queue_service.py's llen() — see DEVLOG.md).
        return self._redis.hgetall(CIRCUIT_KEY) or {}  # type: ignore[return-value]

    def allow_request(self) -> CircuitDecision:
        """Call before attempting a provider call. Transitions OPEN -> HALF_OPEN itself once the
        cooldown has elapsed, so the caller only has to branch on the returned decision."""
        raw = self._read()
        state = CircuitState(raw.get("state", CircuitState.CLOSED))

        if state == CircuitState.OPEN:
            opened_at = float(raw.get("openedAt", 0))
            if time.time() - opened_at >= self._cooldown_seconds:
                self._redis.hset(CIRCUIT_KEY, "state", CircuitState.HALF_OPEN)
                return CircuitDecision(allowed=True, state=CircuitState.HALF_OPEN)
            return CircuitDecision(allowed=False, state=CircuitState.OPEN)

        return CircuitDecision(allowed=True, state=state)

    def record_success(self) -> None:
        """Closes the circuit from half-open (the probe worked) and resets the failure streak —
        a clean success is a reasonable point to forgive past failures rather than requiring the
        sliding window to age them out on its own."""
        self._redis.hset(
            CIRCUIT_KEY,
            mapping={"state": CircuitState.CLOSED, "failureCount": "0", "windowStart": "0"},
        )

    def record_failure(self) -> None:
        raw = self._read()
        state = CircuitState(raw.get("state", CircuitState.CLOSED))
        now = time.time()

        if state == CircuitState.HALF_OPEN:
            # The probe failed — reopen immediately, cooldown restarts from now.
            self._redis.hset(
                CIRCUIT_KEY,
                mapping={"state": CircuitState.OPEN, "openedAt": str(now)},
            )
            return

        window_start = float(raw.get("windowStart", 0))
        if now - window_start > self._window_seconds:
            failure_count = 1
            window_start = now
        else:
            failure_count = int(raw.get("failureCount", 0)) + 1

        updates = {"failureCount": str(failure_count), "windowStart": str(window_start)}
        if failure_count >= self._failure_threshold:
            updates["state"] = CircuitState.OPEN
            updates["openedAt"] = str(now)
        self._redis.hset(CIRCUIT_KEY, mapping=updates)
