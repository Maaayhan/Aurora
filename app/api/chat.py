"""`POST /chat` — API_SPEC.md. Implements the happy path in SEQUENCE_DIAGRAMS.md §1 and the
cancellation path in §2.

The route itself does three things only (ARCHITECTURE.md §7): validate the
request via `ChatRequest` (declared in the signature), orchestrate through
this module's helpers, and return a `StreamingResponse` wired to the SSE
generator. It does not itself implement rate limiting, persistence, or
queueing.
"""

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
from shared.errors import AuroraError, ErrorCode
from shared.schemas import (
    ChatRequest,
    DoneEventData,
    ErrorEventData,
    InferenceTaskPayload,
    TokenEventData,
)

router = APIRouter()
logger = get_logger(__name__)


# 【卷 七.一.一】终点站——前面二到六节看过的所有零件，都在这一个函数里被组装成一次完整的
# 【卷】请求。对照 docs/SEQUENCE_DIAGRAMS.md 第 1 节的时序图，从这里往下逐行读，你应该能
# 【卷】认出每一步对应哪个模块：认证（四.三，通过 Depends 自动注入，函数签名里都看不到显式
# 【卷】调用）→ 限流检查（四.四）→ 找/建会话、存用户消息、取历史上下文（五.一）→ 订阅 Redis
# 【卷】频道 + 派发 Celery 任务（五.二，注意下面这两步的顺序）→ 返回一个绑定了 SSE 生成器的
# 【卷】StreamingResponse。这个函数本身很短，因为它是纯"编排"（orchestration）——具体怎么做
# 【卷】限流、怎么查历史、怎么发任务，全部委托给了下层模块，这正是 ARCHITECTURE.md 第 7 节
# 【卷】强调的"路由函数应该很薄"的实践。
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

    # 【卷 七.一.二】就是五.二.一讲的那个顺序，这里是它真正被调用的地方：先 await subscribe()
    # 【卷】拿到确认订阅成功的 pubsub 对象，再调用 dispatch_inference() 派发任务。这两行代码
    # 【卷】的先后顺序，就是防止"worker 手速太快、消息发出去了但没人在听"这个竞态条件的全部
    # 【卷】机制——没有锁、没有额外的同步原语，纯粹靠"确保订阅这个动作先完成"来保证正确性。
    # Subscribe before dispatch — otherwise a fast worker could publish the first chunk
    # before anyone is listening (ARCHITECTURE.md §5, step 2).
    pubsub = await queue_service.subscribe(redis, request_id)
    queue_service.dispatch_inference(payload)

    queue_depth.set(await queue_service.get_queue_depth(get_broker_redis()))

    return StreamingResponse(
        _sse_generator(request, redis, pubsub, request_id),
        media_type="text/event-stream",
        headers={"X-Request-Id": request_id, "Cache-Control": "no-cache"},
    )


# 【卷 七.一.三】这个生成器是"把 queue_service.forward_events() 吐出来的 SSEEvent 对象，
# 【卷】变成真正写到 HTTP 响应里的字节流"的最后一棒，同时顺手做了三件事：(1) 第一次收到
# 【卷】TokenEventData 时记一次 TTFT，收到 DoneEventData 时用 completion_tokens 反推 TPOT
# 【卷】（对应 四.六.二 的两个指标）；(2) 把 request.is_disconnected 这个 FastAPI 提供的回调，
# 【卷】原封不动传给 forward_events()（对应 五.二.二 讲的轮询超时机制）；(3) finally 块里
# 【卷】判断如果状态还是初始值"cancelled"、且客户端真的断线了，才去调用 mark_cancelled()——
# 【卷】而不是每次生成器结束都无条件标记取消（正常收到 done/error 之后 status 已经被改写了，
# 【卷】不会走到这个分支）。
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

        if status == "cancelled" and await request.is_disconnected():
            await queue_service.mark_cancelled(
                redis, request_id, ttl_seconds=queue_service.CANCEL_KEY_TTL_SECONDS
            )
            logger.info("chat_request_cancelled", duration_seconds=round(duration, 3))
        else:
            logger.info(
                "chat_request_completed",
                status=status,
                duration_seconds=round(duration, 3),
            )

        await pubsub.unsubscribe(queue_service.stream_channel(request_id))
        await pubsub.aclose()
        clear_request_context()
