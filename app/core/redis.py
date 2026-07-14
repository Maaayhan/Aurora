"""Shared async Redis client — DATA_MODEL.md §2 (sessions, rate limiting, pub/sub, cancel flags)."""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_broker_redis() -> Redis:
    """Separate client for the Celery broker DB (`CELERY_BROKER_URL`, distinct from the app's
    `REDIS_URL` db per `.env.example`) — used to inspect queue depth without assuming the broker
    and app share a logical Redis database."""
    return Redis.from_url(get_settings().celery_broker_url, decode_responses=True)
