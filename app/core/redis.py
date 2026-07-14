"""Shared async Redis client — DATA_MODEL.md §2 (sessions, rate limiting, pub/sub, cancel flags)."""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


# 【卷 四.五】为什么要两个客户端，而不是一个 Redis 连接走天下？看 .env.example 就知道：
# 【卷】REDIS_URL 指向 db0（会话缓存/限流/pub-sub/取消标记），CELERY_BROKER_URL 指向 db1
# 【卷】（Celery 自己的任务队列）。虽然物理上是同一个 Redis 实例，但逻辑上是两个独立数据库——
# 【卷】get_broker_redis() 存在的唯一目的是去"偷看"Celery 内部队列的长度（给 aurora_queue_depth
# 【卷】指标用），如果误用 get_redis() 去查，会查到错误的 db，永远查不到真实队列深度。
# 【卷】记住这个坑，六.一（worker/celery_app.py）会看到 Celery 本身怎么连接 broker。
@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_broker_redis() -> Redis:
    """Separate client for the Celery broker DB (`CELERY_BROKER_URL`, distinct from the app's
    `REDIS_URL` db per `.env.example`) — used to inspect queue depth without assuming the broker
    and app share a logical Redis database."""
    return Redis.from_url(get_settings().celery_broker_url, decode_responses=True)
