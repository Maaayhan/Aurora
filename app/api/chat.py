"""`POST /chat` — API_SPEC.md. Implements the happy path in SEQUENCE_DIAGRAMS.md §1 and the
cancellation path in §2.

The route itself does three things only (ARCHITECTURE.md §7): validate the
request via `ChatRequest` (declared in the signature), orchestrate through
this module's helpers, and return a `StreamingResponse` wired to the SSE
generator. It does not itself implement rate limiting, persistence, or
queueing.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Identity, get_current_identity
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.logging import bind_request_id, clear_request_context, get_logger
from app.core.metrics import (
    backpressure_rejections_total,
    circuit_breaker_state,
    inference_errors_total,
    queue_depth,
    rate_limit_rejections_total,
    request_duration_seconds,
    requests_total,
    tpot_seconds,
    ttft_seconds,
)
from app.core.rate_limit import check_rate_limit
from app.core.redis import get_broker_redis, get_redis
from app.services import queue_service, session_service
from shared.circuit import CIRCUIT_KEY, CircuitState
from shared.errors import AuroraError, ErrorCode
from shared.schemas import (
    ChatRequest,
    DoneEventData,
    ErrorEventData,
    InferenceTaskPayload,
    TokenEventData,
)

_CIRCUIT_STATE_GAUGE_VALUE = {
    CircuitState.CLOSED: 0,
    CircuitState.HALF_OPEN: 1,
    CircuitState.OPEN: 2,
}

router = APIRouter()
logger = get_logger(__name__)


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    redis = get_redis()

    rate_limit_result = await check_rate_limit(
        redis,
        identity.id,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    if not rate_limit_result.allowed:
        rate_limit_rejections_total.inc()
        raise AuroraError(
            ErrorCode.RATE_LIMITED,
            "Rate limit exceeded",
            retry_after_seconds=rate_limit_result.retry_after_seconds,
        )

    # Backpressure (ERROR_HANDLING.md §5): checked synchronously, before any Postgres work and
    # before enqueue — a client over quota already got turned away above; this catches everyone
    # else when the *system*, not the caller, is the constraint. Rejecting here is what keeps
    # "the queue got long" from ever becoming "the queue grew unbounded and Redis falls over."
    broker_redis = get_broker_redis()
    current_queue_depth = await queue_service.get_queue_depth(broker_redis)
    queue_depth.set(current_queue_depth)
    if current_queue_depth > settings.max_queue_depth:
        backpressure_rejections_total.inc()
        raise AuroraError(
            ErrorCode.SERVER_BUSY,
            "Server is at capacity",
            retry_after_seconds=5,
        )

    if body.conversation_id:
        conversation = await session_service.get_owned_conversation(
            db, body.conversation_id, identity.id
        )
    else:
        conversation = await session_service.create_conversation(db, identity.id)

    await session_service.append_message(
        db, redis, str(conversation.id), role="user", content=body.message
    )
    context = await session_service.get_context(db, redis, str(conversation.id))

    request_id = str(uuid.uuid4())
    model = body.model or settings.default_model

    payload = InferenceTaskPayload(
        request_id=request_id,
        conversation_id=str(conversation.id),
        user_id=identity.id,
        messages=context,
        model=model,
        enqueued_at=datetime.now(UTC),
    )

    # Subscribe before dispatch — otherwise a fast worker could publish the first chunk
    # before anyone is listening (ARCHITECTURE.md §5, step 2).
    pubsub = await queue_service.subscribe(redis, request_id)
    queue_service.dispatch_inference(payload)

    # circuit:inference lives on the app's own Redis (REDIS_URL), not the Celery broker db —
    # DATA_MODEL.md §2 lists it alongside session/ratelimit/stream/cancel, not the queue itself.
    raw_state = await redis.hget(CIRCUIT_KEY, "state")  # type: ignore[misc] # redis-py stub quirk
    state = CircuitState(raw_state) if raw_state else CircuitState.CLOSED
    circuit_breaker_state.set(_CIRCUIT_STATE_GAUGE_VALUE[state])

    return StreamingResponse(
        _sse_generator(request, redis, pubsub, request_id),
        media_type="text/event-stream",
        headers={"X-Request-Id": request_id, "Cache-Control": "no-cache"},
    )


async def _sse_generator(request: Request, redis, pubsub, request_id: str):
    bind_request_id(request_id)
    start = time.monotonic()
    first_token_at: float | None = None
    status = "cancelled"

    try:
        async for event in queue_service.forward_events(
            pubsub, request_id, is_disconnected=request.is_disconnected
        ):
            now = time.monotonic()
            if isinstance(event.data, TokenEventData) and first_token_at is None:
                first_token_at = now
                ttft_seconds.observe(now - start)
            elif isinstance(event.data, DoneEventData):
                status = "success"
                if first_token_at is not None and event.data.completion_tokens:
                    tpot_seconds.observe(
                        (now - first_token_at) / event.data.completion_tokens
                    )
            elif isinstance(event.data, ErrorEventData):
                status = "error"
                inference_errors_total.labels(code=event.data.code.value).inc()

            yield event.encode()
    finally:
        duration = time.monotonic() - start
        request_duration_seconds.observe(duration)
        requests_total.labels(status=status).inc()

        # `status` stays "cancelled" (its initial value) only if the loop above exited without
        # ever seeing a done/error event — which happens when the client disconnected. By the
        # time execution reaches here, the ASGI server may have *already* cancelled this
        # coroutine directly (uvicorn detects a closed transport independently of, and often
        # faster than, our own is_disconnected() polling inside forward_events) — so this
        # `finally` block can itself be running inside an already-cancelled scope. A bare
        # `await` here would immediately re-raise CancelledError and abort everything below it,
        # silently losing the mark_cancelled() write the worker depends on to actually stop
        # generating — this was a real, reproducible bug: killing a client mid-stream never
        # reached the worker at all, and it ran to completion anyway. asyncio.shield() runs each
        # awaitable to completion in the background even if this coroutine is cancelled again
        # while awaiting it, and the surrounding try/except keeps that re-raised CancelledError
        # from also skipping the logging and pubsub cleanup that follow.
        if status == "cancelled":
            try:
                await asyncio.shield(
                    queue_service.mark_cancelled(
                        redis, request_id, ttl_seconds=queue_service.CANCEL_KEY_TTL_SECONDS
                    )
                )
            except asyncio.CancelledError:
                pass
            logger.info("chat_request_cancelled", duration_seconds=round(duration, 3))
        else:
            logger.info(
                "chat_request_completed",
                status=status,
                duration_seconds=round(duration, 3),
            )

        try:
            await asyncio.shield(pubsub.unsubscribe(queue_service.stream_channel(request_id)))
            await asyncio.shield(pubsub.aclose())
        except asyncio.CancelledError:
            pass
        clear_request_context()
