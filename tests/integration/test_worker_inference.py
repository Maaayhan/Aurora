"""Exercises worker/tasks.py's core async inference loop against MockProvider — no live Ollama,
no live Celery broker needed (ARCHITECTURE.md §4: this is exactly what MockProvider is for).

The load-bearing behavior under test is the retry-eligibility gate from
ERROR_HANDLING.md §3 / SEQUENCE_DIAGRAMS.md §3: a failure is only
retry-eligible if it happened before any token reached the client.
"""

from datetime import UTC, datetime

import fakeredis

import worker.tasks as tasks
from providers.mock_provider import MockProvider
from shared.schemas import ChatMessage, InferenceTaskPayload


def _payload(request_id: str = "req-1") -> InferenceTaskPayload:
    return InferenceTaskPayload(
        request_id=request_id,
        conversation_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        messages=[ChatMessage(role="user", content="hi")],
        model="mock",
        enqueued_at=datetime.now(UTC),
    )


async def test_successful_stream_publishes_tokens_then_reports_success(monkeypatch):
    monkeypatch.setattr(tasks, "_provider", MockProvider(response="hi there"))
    redis_client = fakeredis.FakeRedis(decode_responses=True)

    outcome = await tasks._run_inference_async(_payload(), redis_client)

    assert outcome.status == "success"
    assert outcome.content == "hi there"
    assert outcome.any_tokens_streamed is True
    assert outcome.completion_tokens == 2


async def test_failure_before_any_token_is_not_marked_as_partial_output(monkeypatch):
    monkeypatch.setattr(
        tasks, "_provider", MockProvider(response="hi there", fail_after_chunks=0)
    )
    redis_client = fakeredis.FakeRedis(decode_responses=True)

    outcome = await tasks._run_inference_async(_payload(), redis_client)

    assert outcome.status == "error"
    assert outcome.any_tokens_streamed is False  # -> caller may safely retry


async def test_failure_after_first_token_is_marked_as_partial_output(monkeypatch):
    monkeypatch.setattr(
        tasks, "_provider", MockProvider(response="hi there", fail_after_chunks=1)
    )
    redis_client = fakeredis.FakeRedis(decode_responses=True)

    outcome = await tasks._run_inference_async(_payload(), redis_client)

    assert outcome.status == "error"
    assert outcome.any_tokens_streamed is True  # -> caller must NOT retry, terminal error only


async def test_cancellation_stops_the_stream_without_publishing_a_terminal_event(monkeypatch):
    monkeypatch.setattr(
        tasks, "_provider", MockProvider(response="one two three four five", chunk_delay_seconds=0)
    )
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    payload = _payload("req-cancel")
    redis_client.set(f"cancel:{payload.request_id}", "1")

    outcome = await tasks._run_inference_async(payload, redis_client)

    assert outcome.status == "cancelled"


def test_classify_error_maps_timeout_message_to_inference_timeout():
    from shared.errors import ErrorCode

    assert tasks._classify_error("The model did not respond in time (timeout)") == (
        ErrorCode.INFERENCE_TIMEOUT
    )
    assert tasks._classify_error("connection refused") == ErrorCode.INFERENCE_ERROR
