"""Celery task entrypoint — owns the ModelProvider instance, publishes stream chunks.

Explicitly not responsible for HTTP concerns, auth, or rate limiting
(ARCHITECTURE.md §6 — those already happened in app/ before this task was
ever dispatched). Implements the worker side of SEQUENCE_DIAGRAMS.md §1
(happy path), §2 (cancellation), and §3 (retry vs. fail-loud).
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

import redis as sync_redis
from redis.asyncio import Redis as AsyncRedis

from app.core.config import get_settings
from app.core.db import get_engine, get_session_factory
from app.core.logging import bind_request_id, clear_request_context, configure_logging, get_logger
from app.services.session_service import append_message
from providers.factory import build_provider
from shared.errors import ErrorCode
from shared.schemas import InferenceTaskPayload, StreamMessage
from worker.celery_app import celery_app
from worker.reliability import CancellationChecker, compute_backoff_seconds

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

_provider = build_provider(
    settings.model_provider,
    provider_url=settings.model_provider_url,
    default_model=settings.default_model,
    timeout_seconds=settings.inference_timeout_seconds,
)


def _stream_channel(request_id: str) -> str:
    return f"stream:{request_id}"


def _publish(redis_client: sync_redis.Redis, request_id: str, message: StreamMessage) -> None:
    redis_client.publish(_stream_channel(request_id), message.model_dump_json(by_alias=True))


@dataclass
class InferenceOutcome:
    status: Literal["success", "cancelled", "error"]
    any_tokens_streamed: bool
    content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_message: str | None = None


async def _run_inference_async(
    payload: InferenceTaskPayload, redis_client: sync_redis.Redis
) -> InferenceOutcome:
    cancel_check = CancellationChecker(redis_client, payload.request_id)
    any_tokens = False
    parts: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    try:
        async for chunk in _provider.chat(payload.messages, cancel_check=cancel_check):
            if cancel_check():
                return InferenceOutcome(
                    status="cancelled", any_tokens_streamed=any_tokens, content="".join(parts)
                )
            if chunk.content:
                any_tokens = True
                parts.append(chunk.content)
                _publish(
                    redis_client,
                    payload.request_id,
                    StreamMessage(type="token", content=chunk.content),
                )
            if chunk.finished:
                # Only overwrite when the chunk actually carries numbers: OllamaProvider can
                # yield more than one `finished=True` chunk (a content/finish_reason chunk, then
                # a separate usage-only chunk per `stream_options.include_usage` — see
                # providers/ollama_provider.py) — a later finished chunk with no usage data must
                # not clobber real counts an earlier one already set.
                if chunk.prompt_tokens is not None:
                    prompt_tokens = chunk.prompt_tokens
                if chunk.completion_tokens is not None:
                    completion_tokens = chunk.completion_tokens
    except Exception as exc:  # noqa: BLE001 — any provider failure is a candidate for retry/terminal-error
        logger.warning("inference_call_failed", error=str(exc), any_tokens_streamed=any_tokens)
        return InferenceOutcome(
            status="error",
            any_tokens_streamed=any_tokens,
            content="".join(parts),
            error_message=str(exc),
        )

    # A provider whose generator honors `cancel_check` internally (as `MockProvider` and
    # `OllamaProvider` both do) can exit the loop above with zero iterations — the `if
    # cancel_check()` inside the loop body then never runs. Check once more after the loop so a
    # cancellation that happened before the first chunk isn't misreported as "success".
    if cancel_check():
        return InferenceOutcome(
            status="cancelled", any_tokens_streamed=any_tokens, content="".join(parts)
        )

    return InferenceOutcome(
        status="success",
        any_tokens_streamed=any_tokens,
        content="".join(parts),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def _persist_assistant_message(
    payload: InferenceTaskPayload, outcome: InferenceOutcome, latency_ms: int, retry_count: int
) -> None:
    # Deliberately not app.core.db/redis's shared singletons: those are created once at import
    # time and, for asyncpg/redis-py's async clients, get bound to whichever event loop is
    # running the first time they're actually used. app/ (Uvicorn) has exactly one loop for the
    # process's lifetime, so that's fine there — but each Celery task here runs inside its own
    # fresh `asyncio.run()` call (see `run_inference`), so reusing a pooled connection opened on
    # a *previous* call's loop raises "Future attached to a different loop". Scoping a fresh
    # Redis client to this call, and disposing the engine's pool afterward so the next task
    # reconnects clean, is what actually fixes it — found by running this against a live
    # Postgres/Redis, not by the unit test suite (single event loop throughout).
    redis_client = AsyncRedis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            await append_message(
                db,
                redis_client,
                payload.conversation_id,
                role="assistant",
                content=outcome.content,
                model=payload.model,
                latency_ms=latency_ms,
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                retry_count=retry_count,
            )
    finally:
        await redis_client.aclose()
        await get_engine().dispose()


def _classify_error(message: str | None) -> ErrorCode:
    if message and "timeout" in message.lower():
        return ErrorCode.INFERENCE_TIMEOUT
    return ErrorCode.INFERENCE_ERROR


@celery_app.task(
    bind=True,
    name="worker.tasks.run_inference",
    max_retries=max(settings.retry_max_attempts - 1, 0),
)
def run_inference(self, payload_dict: dict) -> None:
    payload = InferenceTaskPayload.model_validate(payload_dict)
    bind_request_id(payload.request_id)
    redis_client = sync_redis.Redis.from_url(settings.redis_url, decode_responses=True)
    start = time.monotonic()

    try:
        outcome = asyncio.run(_run_inference_async(payload, redis_client))

        if outcome.status == "cancelled":
            logger.info("inference_cancelled")
            return

        if outcome.status == "error":
            attempt_number = self.request.retries + 1
            # Retries are only safe before any token has reached the client (ERROR_HANDLING.md
            # §3, SEQUENCE_DIAGRAMS.md §3) — a failure after partial output is always terminal.
            if not outcome.any_tokens_streamed and self.request.retries < (
                settings.retry_max_attempts - 1
            ):
                backoff = compute_backoff_seconds(
                    attempt_number, base_seconds=settings.retry_backoff_base_seconds
                )
                logger.warning(
                    "inference_retry_scheduled",
                    attempt=attempt_number,
                    backoff_seconds=backoff,
                    error=outcome.error_message,
                )
                raise self.retry(countdown=backoff, exc=RuntimeError(outcome.error_message))

            code = _classify_error(outcome.error_message)
            logger.error(
                "inference_failed_terminal",
                code=code,
                retries=self.request.retries,
                after_partial_output=outcome.any_tokens_streamed,
            )
            _publish(
                redis_client,
                payload.request_id,
                StreamMessage(
                    type="error",
                    code=code,
                    message=outcome.error_message or "Inference failed",
                    retryable=False,
                ),
            )
            return

        latency_ms = int((time.monotonic() - start) * 1000)
        _publish(
            redis_client,
            payload.request_id,
            StreamMessage(
                type="done",
                request_id=payload.request_id,
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                latency_ms=latency_ms,
            ),
        )
        logger.info(
            "inference_completed",
            latency_ms=latency_ms,
            retry_count=self.request.retries,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
        )
        asyncio.run(
            _persist_assistant_message(payload, outcome, latency_ms, self.request.retries)
        )
    finally:
        clear_request_context()
