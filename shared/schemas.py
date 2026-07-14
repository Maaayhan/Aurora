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


# 【卷 二.二.一】这个项目里"数据在几层之间传递时,长相分别是什么样"是个值得记住的知识点：
# 【卷】ChatChunk(Provider 内部,snake_case,Python 属性访问) → StreamMessage(Redis pub/sub 上跑的,
# 【卷】worker 发布/app 订阅) → SSEEvent(最终吐给 HTTP 客户端的,camelCase JSON)。
# 【卷】为什么不是一种格式走到底？因为每一层的"读者"不同：Provider 内部只有 Python 代码在读，
# 【卷】没必要在意 JSON 命名风格；一过 Redis 边界和 HTTP 边界，读者就变成了"跨语言/跨系统的消费者"，
# 【卷】这时候 API_SPEC.md 约定的 camelCase 才有意义。CamelModel 就是"Python 端用 snake_case 写，
# 【卷】序列化成 JSON 时自动转 camelCase"的统一底座，两头都不用手动 alias。
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


# 【卷 二.二.二】重点看 messages 字段：这是"完整的对话历史"，不是"这一条新消息"。也就是说，
# 【卷】app/ 在派发任务之前，已经把 Redis 缓存 + Postgres 兜底查出来的历史上下文拼好，
# 【卷】一次性塞进这个 payload 里。为什么不让 worker 自己去查历史？因为这样 worker 的
# 【卷】唯一外部依赖就只剩"模型服务"了——不用连 Postgres/Redis 也能独立测试
# 【卷】（见 providers/mock_provider.py），
# 【卷】而且这个 payload 本身就是"这次请求到底发了什么给模型"的完整快照，出问题了可以直接拿它复现，
# 【卷】不用去猜"当时历史记录是什么样"。
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
