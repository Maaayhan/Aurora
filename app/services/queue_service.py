"""Dispatches the Celery task, owns the pub/sub subscribe/forward bridge — ARCHITECTURE.md §5-6.

Explicitly not responsible for executing inference — that's `worker/tasks.py`,
in a separate process, reached only through Celery + Redis pub/sub. This
module builds its own minimal Celery *producer* client (broker connection
only) rather than importing `worker.celery_app`, so the API process/image
never needs the `worker/` package on disk at all — not just "app/ never
imports a provider class" (ARCHITECTURE.md §6), but no import dependency on
`worker/` in the first place. Dispatch is by task *name* (`send_task`), the
standard Celery pattern for a producer that doesn't need the consumer's task
definitions loaded.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

from celery import Celery
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.core.config import get_settings
from shared.errors import ErrorCode
from shared.schemas import (
    DoneEventData,
    ErrorEventData,
    InferenceTaskPayload,
    SSEEvent,
    StreamMessage,
    TokenEventData,
)

_celery_producer = Celery("aurora-producer", broker=get_settings().celery_broker_url)

CELERY_DEFAULT_QUEUE = "celery"
CANCEL_KEY_TTL_SECONDS = 5 * 60  # DATA_MODEL.md §2 — single source of truth for this TTL
POLL_TIMEOUT_SECONDS = 1.0


def stream_channel(request_id: str) -> str:
    return f"stream:{request_id}"


def cancel_key(request_id: str) -> str:
    return f"cancel:{request_id}"


async def subscribe(redis: Redis, request_id: str) -> PubSub:
    """Must be called and awaited (subscription confirmed) before `dispatch_inference` — otherwise
    a fast worker could publish the first chunk before anyone is listening (ARCHITECTURE.md §5,
    step 2)."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(stream_channel(request_id))
    return pubsub


def dispatch_inference(payload: InferenceTaskPayload) -> None:
    _celery_producer.send_task(
        "worker.tasks.run_inference",
        kwargs={"payload_dict": payload.model_dump(mode="json", by_alias=True)},
    )


async def forward_events(
    pubsub: PubSub,
    request_id: str,
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    poll_timeout: float = POLL_TIMEOUT_SECONDS,
) -> AsyncIterator[SSEEvent]:
    """Forwards `stream:{request_id}` pub/sub messages as SSE events, in the order received, until
    a terminal `done`/`error` message closes the stream (ARCHITECTURE.md §5, steps 3-5).

    Polls with a timeout (rather than a blocking `pubsub.listen()`) specifically so the caller
    can interleave a client-disconnect check between messages — SEQUENCE_DIAGRAMS.md §2 needs the
    API to notice a closed connection even while no new chunk has arrived yet.
    """
    while True:
        if is_disconnected is not None and await is_disconnected():
            return

        raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_timeout)
        if raw is None:
            continue

        msg = StreamMessage.model_validate_json(raw["data"])

        if msg.type == "token":
            yield SSEEvent(event="token", data=TokenEventData(content=msg.content or ""))
        elif msg.type == "done":
            yield SSEEvent(
                event="done",
                data=DoneEventData(
                    request_id=msg.request_id or request_id,
                    prompt_tokens=msg.prompt_tokens or 0,
                    completion_tokens=msg.completion_tokens or 0,
                    latency_ms=msg.latency_ms or 0,
                ),
            )
            return
        elif msg.type == "error":
            yield SSEEvent(
                event="error",
                data=ErrorEventData(
                    code=msg.code or ErrorCode.INFERENCE_ERROR,
                    message=msg.message or "Inference failed",
                    retryable=msg.retryable or False,
                ),
            )
            return


async def mark_cancelled(redis: Redis, request_id: str, *, ttl_seconds: int) -> None:
    """Set on client disconnect (SEQUENCE_DIAGRAMS.md §2) — polled by the worker between chunks."""
    await redis.set(cancel_key(request_id), "1", ex=ttl_seconds)


async def get_queue_depth(broker_redis: Redis) -> int:
    """Approximate depth of the default Celery queue (its Redis broker list) — used for the
    `aurora_queue_depth` metric (ROADMAP.md Phase 1) and, from Phase 2 on, backpressure rejection
    (ERROR_HANDLING.md §5). Takes the broker Redis client specifically (`CELERY_BROKER_URL`), not
    the app's general-purpose Redis client — they're different logical databases."""
    return await broker_redis.llen(CELERY_DEFAULT_QUEUE)  # type: ignore[misc] # redis-py stub quirk
