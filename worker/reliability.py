"""Retry backoff + cooperative cancellation — ERROR_HANDLING.md §2-3, SEQUENCE_DIAGRAMS.md §2.

Circuit breaker (ERROR_HANDLING.md §4) is deliberately NOT implemented here
yet — ROADMAP.md Phase 1 scope is explicit that retry-with-backoff lands
without a circuit breaker, which is Phase 2 scope. Adding it now would violate
the roadmap's non-negotiable ordering rule (Phase 2 hardening doesn't start
until Phase 1's definition of done is real).

Retries themselves use Celery's native `autoretry_for` / `self.retry(countdown=...)`
(ARCHITECTURE.md §2) — this module only computes the backoff schedule and
answers "has any token already reached the client," the gate that decides
whether a failure is retryable at all (ERROR_HANDLING.md §3).
"""

from redis.client import Redis as SyncRedis


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
