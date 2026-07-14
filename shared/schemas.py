"""Pydantic models shared across app/ and worker/ — DATA_MODEL.md §3, API_SPEC.md.

Defined once here and imported by both processes so the task payload, the SSE
event shapes, and the provider chat contract can never silently drift apart
across the API/worker boundary (ARCHITECTURE.md §6, `shared/`).

JSON on the wire is camelCase (API_SPEC.md examples); Python attribute access
stays snake_case via `alias_generator` + `populate_by_name`.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from shared.errors import ErrorCode


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChatMessage(CamelModel):
    """One turn of conversation context, in the shape sent to the model provider."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatChunk(CamelModel):
    """One incremental piece of a streamed model response — the `ModelProvider.chat` yield type."""

    content: str
    finished: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ProviderHealth(CamelModel):
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None


class InferenceTaskPayload(CamelModel):
    """The Celery task payload — DATA_MODEL.md §3.

    Full message context is resolved by app/ before dispatch, not by worker/,
    so the worker's only external dependency stays the model provider and a
    request is fully replayable from this payload alone.
    """

    request_id: str
    conversation_id: str
    user_id: str
    messages: list[ChatMessage]
    model: str
    enqueued_at: datetime


# --- SSE event payloads (API_SPEC.md `POST /chat`) ---


class TokenEventData(CamelModel):
    content: str


class DoneEventData(CamelModel):
    request_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ErrorEventData(CamelModel):
    code: ErrorCode
    message: str
    retryable: bool


class SSEEvent(BaseModel):
    """Wire shape for one SSE frame: `event: {event}\\ndata: {json(data)}\\n\\n`."""

    event: Literal["token", "done", "error"]
    data: TokenEventData | DoneEventData | ErrorEventData

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {self.data.model_dump_json(by_alias=True)}\n\n"


# --- Redis pub/sub message shape on `stream:{request_id}` (ARCHITECTURE.md §5) ---
# Published by worker/, consumed by app/'s subscribe/forward bridge and re-emitted as an SSEEvent.


class StreamMessage(CamelModel):
    type: Literal["token", "done", "error"]
    content: str | None = None
    request_id: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    code: ErrorCode | None = None
    message: str | None = None
    retryable: bool | None = None


class ChatRequest(CamelModel):
    """`POST /chat` request body — API_SPEC.md."""

    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    model: str | None = None
