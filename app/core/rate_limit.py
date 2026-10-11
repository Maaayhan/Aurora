"""Sliding window log rate limiter — ERROR_HANDLING.md §1.

Per `identityId`, stored in Redis as a sorted set (`ratelimit:{identityId}`,
score = request timestamp). Chosen over fixed windows (which allow a 2x-limit
burst at a window boundary) and over token bucket (which is the right tool
for "smoothed throughput with burst allowance," not the flat "N requests per
window" requirement here).

The whole decision runs as one Lua script, so Redis executes it atomically.
Issuing the check (`ZCARD`) and the add (`ZADD`) as separate commands lets
concurrent requests all observe `count < limit` before any of them has added
its entry, and every one of them is let through.

Explicitly not responsible for queue depth or inference concurrency
(`ARCHITECTURE.md` §6) — this only decides "is this identity over its request
quota," before the request ever reaches the queue.
"""

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

RATE_LIMIT_KEY_PREFIX = "ratelimit:"

# KEYS[1] = window key; ARGV = now, window_seconds, limit, unique member.
# Returns {1, "0"} when allowed, {0, "<oldest entry timestamp>"} when rejected.
# Reject-before-add: a rejected request is never counted, so a client retrying
# right after `Retry-After` sees a clean window rather than one polluted by the
# rejected attempt. The oldest timestamp is returned as a string because Redis
# truncates Lua floats to integers.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

if redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local oldest_ts = ARGV[1]
  if #oldest > 0 then
    oldest_ts = oldest[2]
  end
  return {0, oldest_ts}
end

redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return {1, '0'}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


async def check_rate_limit(
    redis: Redis, identity_id: str, *, limit: int, window_seconds: int
) -> RateLimitResult:
    now = time.time()
    # A unique member per request: two requests in the same clock tick must not
    # collapse into one sorted-set entry and be under-counted.
    member = f"{now}:{uuid.uuid4().hex}"

    allowed, oldest_ts = await redis.eval(  # type: ignore[misc]
        _SLIDING_WINDOW_SCRIPT,
        1,
        f"{RATE_LIMIT_KEY_PREFIX}{identity_id}",
        str(now),
        str(window_seconds),
        str(limit),
        member,
    )
    if int(allowed) == 1:
        return RateLimitResult(allowed=True, retry_after_seconds=0)

    retry_after = max(1, int(float(oldest_ts) + window_seconds - now))
    return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
