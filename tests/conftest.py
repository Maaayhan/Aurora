"""Shared fixtures for integration tests.

Postgres -> in-memory SQLite (via aiosqlite): fine for these tests because
nothing here exercises Postgres-specific SQL, only the ORM layer.
Redis -> fakeredis: DATA_MODEL.md's Redis usage (sorted sets, pub/sub,
string TTLs) is all supported by fakeredis's async client.
Celery -> `queue_service.dispatch_inference` is monkeypatched to a fake that
publishes the same pub/sub messages a real worker would, so tests exercise
the real subscribe-before-dispatch bridge without a live broker.
"""

import asyncio

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import hash_api_key
from app.core.db import Base
from app.core.models import User


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def db_session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def test_user(db_session_factory):
    async with db_session_factory() as db:
        user = User(api_key_hash=hash_api_key("sk-test-key"))
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


@pytest.fixture
async def app_client(monkeypatch, db_session_factory, fake_redis):
    """A `httpx.AsyncClient` wired to the real FastAPI app, with Postgres -> sqlite and
    Redis -> fakeredis substituted at every entry point that constructs its own client."""
    from app.api import chat as chat_module
    from app.api import health as health_module
    from app.core.db import get_db
    from app.main import app

    async def override_get_db():
        async with db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    monkeypatch.setattr(chat_module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(chat_module, "get_broker_redis", lambda: fake_redis)
    monkeypatch.setattr(health_module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(health_module, "get_broker_redis", lambda: fake_redis)
    health_module._health_provider.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


# 【卷 八.一】这个 fixture 本身就踩过一个坑，很值得作为收官案例：第一版只做了"往 Redis 频道
# 【卷】发布 token/done 消息"，完全没做"把助手回复写进 Postgres"这件事——结果测试多轮对话、
# 【卷】反馈接口的用例全部失败，报的还是 StopIteration 这种看起来跟本意完全不相关的错误
# 【卷】（因为测试代码里 `next(m for m in messages if m["role"]=="assistant")` 在空列表上
# 【卷】找不到东西）。根因是：持久化到 Postgres 本来就是真实 worker（六.三.三）的职责，
# 【卷】这个 fake 只模拟了"消息总线"那一半，没模拟"worker 真正做的另一半事情"——这提醒我们，
# 【卷】写测试替身（fake/mock/stub）的时候，必须先想清楚"真实实现到底承担了哪些职责"，
# 【卷】漏掉一半职责的 fake，会让测试看起来在测，实际上只测了一半系统行为。
@pytest.fixture
def fake_dispatch(monkeypatch, fake_redis, db_session_factory):
    """Stands in for a real Celery worker: publishes a canned token stream to
    `stream:{request_id}` the moment a task would have been dispatched, so
    `POST /chat`'s SSE response has something to forward without a live broker —
    and also persists the assistant reply, mirroring `worker/tasks.py`'s
    `_persist_assistant_message` (SEQUENCE_DIAGRAMS.md §1's "task result persisted
    to Postgres" step), since that's the real worker's job, not the API's."""
    from app.services import queue_service, session_service

    def _dispatch(payload):
        async def _publish_then_persist():
            await asyncio.sleep(0)
            await fake_redis.publish(
                queue_service.stream_channel(payload.request_id),
                '{"type":"token","content":"Hello"}',
            )
            # Persisted *before* publishing `done`, unlike the real worker (which persists
            # after — SEQUENCE_DIAGRAMS.md §1 marks that step "(async)" precisely because it's
            # not guaranteed to land before the client's next request). Reordered here only so
            # this test double is deterministic; the real race is a documented, accepted
            # tradeoff, not something a test fixture should paper over silently — noting it here
            # so it doesn't look like an oversight.
            async with db_session_factory() as db:
                await session_service.append_message(
                    db,
                    fake_redis,
                    payload.conversation_id,
                    role="assistant",
                    content="Hello",
                    model=payload.model,
                    latency_ms=5,
                    prompt_tokens=1,
                    completion_tokens=1,
                )
            done_payload = (
                f'{{"type":"done","requestId":"{payload.request_id}",'
                '"promptTokens":1,"completionTokens":1,"latencyMs":5}'
            )
            await fake_redis.publish(
                queue_service.stream_channel(payload.request_id), done_payload
            )

        asyncio.create_task(_publish_then_persist())

    monkeypatch.setattr(queue_service, "dispatch_inference", _dispatch)
    return _dispatch
