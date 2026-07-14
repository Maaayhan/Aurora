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


# 【卷 四.四.一】滑动窗口日志算法，用 Redis 的有序集合（sorted set）实现，一次请求 4 步：
# 【卷】(1) ZREMRANGEBYSCORE 删掉窗口外的旧时间戳 (2) ZCARD 数一下窗口内还剩多少条
# 【卷】(3) 够了就拒绝，不够就 ZADD 加一条当前时间戳 (4) EXPIRE 续期整个 key，避免不活跃用户
# 【卷】的 key 永远留在 Redis 里。跟"固定窗口计数器"比：固定窗口在整点边界重置，理论上攻击者
# 【卷】可以在 23:59:59 发一波、00:00:00 再发一波，等于 1 分钟内打进 2 倍限额——滑动窗口不存在
# 【卷】这个问题，因为"窗口"永远是"以当前时刻为终点，往前推 N 秒"，不会有固定的重置边界。
# 【卷】跟"令牌桶"比：令牌桶解决的是"允许一定程度的突发流量"，这里的需求恰恰相反——
# 【卷】"N 秒内最多 M 次"是硬限制，不允许突发，所以令牌桶不是这个场景该用的工具。
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

    # 【卷 四.四.二】关键细节："先判断是否超限，超限就直接返回，不执行下面的 ZADD"——
    # 【卷】也就是说一次被拒绝的请求，根本不会往有序集合里写自己的时间戳。这样设计是为了让
    # 【卷】"limit 之上的流量不会污染窗口"：如果拒绝的请求也被记进去，客户端疯狂重试反而会让
    # 【卷】窗口一直保持"满"的状态，永远等不到解封。
    if count >= limit:
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        oldest_ts = oldest[0][1] if oldest else now
        retry_after = max(1, int(oldest_ts + window_seconds - now))
        return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

    await redis.zadd(key, {str(now): now})
    await redis.expire(key, window_seconds)
    return RateLimitResult(allowed=True, retry_after_seconds=0)
