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

    # 【卷 六.三.一】这是 DEVLOG.md 问题 #8 的真实现场，被测试直接抓到。原来的代码逻辑是：
    # 【卷】"async for 循环里每轮检查一次 cancel_check()"——听起来天衣无缝，但漏了一种情况：
    # 【卷】如果客户端在请求一开始、Provider 还没吐出第一个 token 之前就已经取消了，
    # 【卷】MockProvider/OllamaProvider 内部自己也在检查 cancel_check（它们的 chat() 实现里
    # 【卷】同样会 poll 这个标记），会直接返回一个"一次都没 yield 过"的空生成器。这样一来，
    # 【卷】外层这个 `async for chunk in ...` 循环体一次都不会执行，里面那句 `if cancel_check()`
    # 【卷】自然也从来没机会跑——函数直接掉到最后，落进"success"分支，把一次取消误报成了成功。
    # 【卷】修法很简单：循环走完之后，不管是正常结束还是零次迭代，都再问一次 cancel_check()。
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
    # 【卷 六.三.三】全场最有技术深度的一个 bug（DEVLOG.md 问题 #13），建议逐字读完这段注释。
    #
    # 先回忆 四.二.一 埋的伏笔：app/core/db.py 里的 _engine / _session_factory 是模块加载时
    # 创建一次的全局单例。这个设计的隐含前提是"创建它的那个 asyncio 事件循环，会一直存活到
    # 进程结束"——对 app/（FastAPI 跑在 Uvicorn 里，从启动到关闭只有一个事件循环）完全成立。
    #
    # 但 worker 的执行模型不一样：run_inference（六.三.二）是一个普通的同步函数（Celery task
    # 本体不是 async def），它内部用 asyncio.run(...) 来跑异步逻辑——而 asyncio.run() 每次
    # 调用都会创建一个全新的事件循环，跑完就把这个循环彻底关掉。第一次任务调用 asyncio.run()
    # 时，如果这时候才第一次真正用到 app.core.db 的那个全局 engine，它底层的 asyncpg 连接
    # 会绑定在"这一次"的事件循环上；等这次 asyncio.run() 结束、循环被关闭，这些连接其实已经
    # 死了，但连接池对象本身还留在内存里，等着"下一次"被拿出来复用。
    #
    # 到了第二个任务，run_inference 又调用一次全新的 asyncio.run()——又是一个全新的事件循环。
    # 这次如果还是用同一个全局单例 engine，连接池会把第一次任务遗留下来的、绑定在"已经死掉的
    # 循环"上的连接拿出来用——于是就崩了：`RuntimeError: Future ... attached to a different
    # loop`。这个 bug 的阴险之处在于：本地单元测试全程只有 pytest-asyncio 提供的同一个事件
    # 循环，永远不会触发"跨循环复用连接"这个条件，所以 39 个测试全绿、mypy/ruff 全过，直到
    # 真正起了一个 Celery worker 进程、连续发了两次真实请求，才在第二次请求上炸出来。
    #
    # 修法：不用共享单例，这个函数自己开一个"生命周期只跟这一次 asyncio.run() 一样长"的
    # Redis 客户端，用完显式关掉；Postgres 那边则是在用完之后调用 get_engine().dispose()，
    # 把连接池清空——这样"下一次" asyncio.run() 再用到这个 engine 时，拿到的一定是在
    # "当前这个循环"里全新建立的连接，不会跟任何已经死掉的循环产生瓜葛。
    #
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

        # 【卷 六.三.二】这里是"重试还是终态报错"的核心判断，一定要吃透这个 if 条件：
        # 【卷】`not outcome.any_tokens_streamed`——已经有 token 发给客户端了，就绝不允许重试，
        # 【卷】哪怕重试次数还没用完。原因很直接：客户端已经在屏幕上渲染了半句话，你没法让它
        # 【卷】"撤回重来"，这时候唯一诚实的选择就是报一个终态错误，让半句话保持半句话的样子，
        # 【卷】而不是悄悄在后台重新生成、再补发一段可能完全不连贯的内容。
        # 【卷】`raise self.retry(countdown=backoff, ...)` 用的是 Celery 原生机制，不是自己写
        # 【卷】一个 while 循环重试——好处是 self.request.retries 这个计数器由 Celery 框架维护，
        # 【卷】配合六.一.一的 task_acks_late，就算这次重试期间 worker 进程本身挂了，任务被
        # 【卷】另一个 worker 捞起来时，重试计数依然是准的，不会因为进程重启就"重置成 0 次"。
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
