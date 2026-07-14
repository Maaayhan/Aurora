"""Sliding window log rate limiter — ERROR_HANDLING.md §1.

Per `identityId`, stored in Redis as a sorted set (`ratelimit:{identityId}`,
score = request timestamp). Chosen over fixed windows (which allow a 2x-limit
burst at a window boundary) and over token bucket (which is the right tool
for "smoothed throughput with burst allowance," not the flat "N requests per
window" requirement here).

Explicitly not responsible for queue depth or inference concurrency
(`ARCHITECTURE.md` §6) — this only decides "is this identity over its request
quota," before the request ever reaches the queue.
"""

import time
from dataclasses import dataclass

from redis.asyncio import Redis

RATE_LIMIT_KEY_PREFIX = "ratelimit:"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


async def check_rate_limit(
    redis: Redis, identity_id: str, *, limit: int, window_seconds: int
) -> RateLimitResult:
    """Reject-before-add: a request that would exceed the limit is never counted, so a client
    retrying immediately after `Retry-After` elapses sees a clean window rather than one still
    polluted by the rejected attempt."""
    key = f"{RATE_LIMIT_KEY_PREFIX}{identity_id}"
    now = time.time()
    window_start = now - window_seconds

    await redis.zremrangebyscore(key, 0, window_start)
    count = await redis.zcard(key)

    if count >= limit:
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        oldest_ts = oldest[0][1] if oldest else now
        retry_after = max(1, int(oldest_ts + window_seconds - now))
        return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

    await redis.zadd(key, {str(now): now})
    await redis.expire(key, window_seconds)
    return RateLimitResult(allowed=True, retry_after_seconds=0)
