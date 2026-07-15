"""Integration tests for Phase 2 hardening inside worker/tasks.py:run_inference — queue-wait
timeout and circuit breaker gating, both of which must reject *before* ever calling the model
provider (ERROR_HANDLING.md §2, §4).

`run_inference` is a bound Celery task (`bind=True`) that builds its own sync Redis client
internally rather than accepting one as a parameter — `Task.apply()` runs it synchronously with a
real task context (`self.request`, retry machinery) without needing a live broker, and
`redis.Redis.from_url` is monkeypatched to return a fakeredis client so no real Redis is needed
either.
"""

import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import fakeredis

import worker.tasks as tasks
from providers.mock_provider import MockProvider
from shared.circuit import CIRCUIT_KEY, CircuitState
from shared.errors import ErrorCode
from shared.schemas import ChatChunk, ChatMessage, InferenceTaskPayload


class _CountingProvider:
    """Wraps MockProvider and counts calls to chat() — used to assert the provider was never
    reached when a Phase 2 gate should have rejected the task first."""

    def __init__(self, inner: MockProvider) -> None:
        self._inner = inner
        self.call_count = 0

    async def chat(
        self, messages: list[ChatMessage], *, cancel_check: Callable[[], bool]
    ) -> AsyncIterator[ChatChunk]:
        self.call_count += 1
        async for chunk in self._inner.chat(messages, cancel_check=cancel_check):
            yield chunk

    async def health_check(self):
        return await self._inner.health_check()


def _payload(request_id: str = "req-1", *, enqueued_at: datetime | None = None) -> dict:
    return InferenceTaskPayload(
        request_id=request_id,
        conversation_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        messages=[ChatMessage(role="user", content="hi")],
        model="mock",
        enqueued_at=enqueued_at or datetime.now(UTC),
    ).model_dump(mode="json", by_alias=True)


def _install_fake_redis(monkeypatch) -> fakeredis.FakeRedis:
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        tasks.sync_redis.Redis, "from_url", classmethod(lambda cls, *a, **kw: fake)
    )
    return fake


def test_queue_wait_timeout_rejects_before_calling_provider(monkeypatch):
    _install_fake_redis(monkeypatch)
    provider = _CountingProvider(MockProvider(response="hi"))
    monkeypatch.setattr(tasks, "_provider", provider)
    monkeypatch.setattr(tasks.settings, "queue_wait_timeout_seconds", 60)

    published = []
    monkeypatch.setattr(
        tasks, "_publish", lambda redis_client, request_id, message: published.append(message)
    )

    stale_enqueued_at = datetime.now(UTC) - timedelta(seconds=120)
    payload = _payload("req-stale", enqueued_at=stale_enqueued_at)

    tasks.run_inference.apply(kwargs={"payload_dict": payload})

    assert provider.call_count == 0
    assert len(published) == 1
    assert published[0].type == "error"
    assert published[0].code == ErrorCode.SERVER_BUSY


def test_fresh_task_is_not_rejected_by_queue_wait_timeout(monkeypatch):
    _install_fake_redis(monkeypatch)
    provider = _CountingProvider(MockProvider(response="hi"))
    monkeypatch.setattr(tasks, "_provider", provider)
    monkeypatch.setattr(tasks.settings, "queue_wait_timeout_seconds", 60)

    payload = _payload("req-fresh")
    tasks.run_inference.apply(kwargs={"payload_dict": payload})

    assert provider.call_count == 1


def test_open_circuit_rejects_before_calling_provider(monkeypatch):
    fake_redis = _install_fake_redis(monkeypatch)
    provider = _CountingProvider(MockProvider(response="hi"))
    monkeypatch.setattr(tasks, "_provider", provider)

    # Pre-seed an OPEN circuit whose cooldown has not elapsed yet.
    fake_redis.hset(
        CIRCUIT_KEY,
        mapping={
            "state": CircuitState.OPEN,
            "failureCount": "5",
            "windowStart": "0",
            "openedAt": str(time.time()),
        },
    )

    published = []
    monkeypatch.setattr(
        tasks, "_publish", lambda redis_client, request_id, message: published.append(message)
    )

    payload = _payload("req-circuit-open")
    tasks.run_inference.apply(kwargs={"payload_dict": payload})

    assert provider.call_count == 0
    assert len(published) == 1
    assert published[0].type == "error"
    assert published[0].code == ErrorCode.INFERENCE_UNAVAILABLE


def test_successful_task_records_circuit_breaker_success(monkeypatch):
    fake_redis = _install_fake_redis(monkeypatch)
    provider = _CountingProvider(MockProvider(response="hi there"))
    monkeypatch.setattr(tasks, "_provider", provider)
    # Pretend the circuit had some prior failures short of the threshold.
    fake_redis.hset(
        CIRCUIT_KEY,
        mapping={"state": CircuitState.CLOSED, "failureCount": "2", "windowStart": "0"},
    )

    payload = _payload("req-success")
    tasks.run_inference.apply(kwargs={"payload_dict": payload})

    assert provider.call_count == 1
    assert fake_redis.hget(CIRCUIT_KEY, "failureCount") == "0"


def test_terminal_failure_records_circuit_breaker_failure(monkeypatch):
    fake_redis = _install_fake_redis(monkeypatch)
    provider = _CountingProvider(MockProvider(response="hi", fail_after_chunks=0))
    monkeypatch.setattr(tasks, "_provider", provider)
    # No retries -> immediately terminal.
    monkeypatch.setattr(tasks.settings, "retry_max_attempts", 1)

    payload = _payload("req-fail")
    tasks.run_inference.apply(kwargs={"payload_dict": payload})

    assert provider.call_count == 1
    assert fake_redis.hget(CIRCUIT_KEY, "failureCount") == "1"
