"""Conversation CRUD + history retrieval — ARCHITECTURE.md §6.

Postgres (`messages` table) is the durable system of record; Redis
(`session:{conversationId}`) is a cache of the *recent* window so a
multi-turn conversation doesn't hit Postgres on every single turn
(DATA_MODEL.md §2). Explicitly not responsible for deciding when a
conversation is "done" — that's a client-driven concept, not a server-side
state machine.
"""

import json
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Conversation, Feedback, Message
from shared.errors import AuroraError, ErrorCode
from shared.schemas import ChatMessage

SESSION_CACHE_TTL_SECONDS = 24 * 60 * 60
SESSION_CACHE_MAX_MESSAGES = 50


def _session_key(conversation_id: str) -> str:
    return f"session:{conversation_id}"


@dataclass(frozen=True)
class MessageRecord:
    id: str
    role: str
    content: str
    created_at: str


async def create_conversation(
    db: AsyncSession, user_id: str, *, title: str | None = None
) -> Conversation:
    conversation = Conversation(user_id=uuid.UUID(user_id), title=title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_owned_conversation(
    db: AsyncSession, conversation_id: str, user_id: str
) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == uuid.UUID(conversation_id),
            Conversation.user_id == uuid.UUID(user_id),
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise AuroraError(ErrorCode.CONVERSATION_NOT_FOUND, "Conversation not found")
    return conversation


async def list_conversations(
    db: AsyncSession, user_id: str, *, limit: int = 20, cursor: str | None = None
) -> list[Conversation]:
    query = (
        select(Conversation)
        .where(Conversation.user_id == uuid.UUID(user_id))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    if cursor:
        query = query.where(Conversation.updated_at < cursor)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_messages(
    db: AsyncSession, conversation_id: str, *, limit: int = 50, cursor: str | None = None
) -> list[Message]:
    query = (
        select(Message)
        .where(Message.conversation_id == uuid.UUID(conversation_id))
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    if cursor:
        query = query.where(Message.created_at > cursor)
    result = await db.execute(query)
    return list(result.scalars().all())


async def append_message(
    db: AsyncSession,
    redis: Redis,
    conversation_id: str,
    *,
    role: str,
    content: str,
    model: str | None = None,
    latency_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    retry_count: int = 0,
) -> Message:
    message = Message(
        conversation_id=uuid.UUID(conversation_id),
        role=role,
        content=content,
        model=model,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        retry_count=retry_count,
    )
    db.add(message)
    # 【卷 五.一.一】这里用 func.now()（让数据库自己算当前时间），而不是 message.created_at
    # 【卷】（Python 对象里的属性）——这是 DEVLOG.md 问题 #7 的真实现场。message.created_at
    # 【卷】是一个 server_default=func.now() 的字段，只有真正写入数据库之后才会被赋值，
    # 【卷】这里 db.add() 只是把对象加进这次事务的"待提交"队列，还没 flush/commit，
    # 【卷】此时读 message.created_at 在 Python 里就是 None——如果直接拿它去更新
    # 【卷】conversations.updated_at（NOT NULL 字段），第二条消息开始就会报约束错误。
    # 【卷】被单元测试当场抓到，修法就是让数据库自己算这个时间戳，不依赖 Python 对象的状态。
    await db.execute(
        update(Conversation)
        .where(Conversation.id == uuid.UUID(conversation_id))
        .values(updated_at=func.now())
    )
    await db.commit()
    await db.refresh(message)

    # 【卷 五.一.二】写 Postgres 的同时也更新 Redis 缓存（write-through），跟下面 get_context()
    # 【卷】的 cache-aside 读取配合，保证"写完之后马上读"不会读到 Postgres 里的旧数据。
    await _cache_append(redis, conversation_id, role=role, content=content)
    return message


async def get_context(
    db: AsyncSession,
    redis: Redis,
    conversation_id: str,
    *,
    window: int = SESSION_CACHE_MAX_MESSAGES,
) -> list[ChatMessage]:
    """Resolve the full message context to send the model — cache-aside over Redis, falling
    back to Postgres on a cache miss (DATA_MODEL.md §3: this happens in app/, before task
    dispatch, so the worker's only dependency stays the model provider)."""
    # 【卷 五.一.三】这是标准的 cache-aside（旁路缓存）读取模式：先查 Redis，命中就直接返回；
    # 【卷】没命中（比如缓存过期、或者这是这个会话第一次被读）就查 Postgres 兜底，查完顺手把结果
    # 【卷】写回 Redis，让下一次读能命中。对照 DATA_MODEL.md 第 4 节："为什么不能只用一个存储"——
    # 【卷】Postgres 保证数据不丢，Redis 保证"同一个会话反复对话"时不用每一轮都查数据库。
    cached = await redis.get(_session_key(conversation_id))
    if cached is not None:
        raw = json.loads(cached)
        return [ChatMessage(role=m["role"], content=m["content"]) for m in raw]

    messages = await list_messages(db, conversation_id, limit=window)
    raw = [{"role": m.role, "content": m.content} for m in messages]
    await redis.set(
        _session_key(conversation_id), json.dumps(raw), ex=SESSION_CACHE_TTL_SECONDS
    )
    return [ChatMessage(role=m["role"], content=m["content"]) for m in raw]


async def _cache_append(redis: Redis, conversation_id: str, *, role: str, content: str) -> None:
    key = _session_key(conversation_id)
    cached = await redis.get(key)
    raw = json.loads(cached) if cached is not None else []
    raw.append({"role": role, "content": content})
    raw = raw[-SESSION_CACHE_MAX_MESSAGES:]
    await redis.set(key, json.dumps(raw), ex=SESSION_CACHE_TTL_SECONDS)


async def add_feedback(db: AsyncSession, message_id: str, user_id: str, rating: int) -> Feedback:
    result = await db.execute(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.id == uuid.UUID(message_id), Conversation.user_id == uuid.UUID(user_id))
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise AuroraError(ErrorCode.CONVERSATION_NOT_FOUND, "Message not found")

    feedback = Feedback(message_id=message.id, rating=rating)
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback
