"""Retry backoff + cooperative cancellation — ERROR_HANDLING.md §2-3, SEQUENCE_DIAGRAMS.md §2.

Circuit breaker (ERROR_HANDLING.md §4) is deliberately NOT implemented here
yet — ROADMAP.md Phase 1 scope is explicit that retry-with-backoff lands
without a circuit breaker, which is Phase 2 scope. Adding it now would violate
the roadmap's non-negotiable ordering rule (Phase 2 hardening doesn't start
until Phase 1's definition of done is real).

Retries themselves use Celery's native `autoretry_for` / `self.retry(countdown=...)`
(ARCHITECTURE.md §2) — this module only computes the backoff schedule and
answers "has any token already reached the client," the gate that decides
whether a failure is retryable at all (ERROR_HANDLING.md §3).
"""

from redis.client import Redis as SyncRedis


# 【卷 六.二.一】指数退避（exponential backoff）：第 1 次重试等 0.5 秒，第 2 次等 1 秒，
# 【卷】第 3 次等 2 秒——每次翻倍。这里只是"算一下该等多久"，真正"等完之后重试"的动作是
# 【卷】Celery 自己的 self.retry(countdown=...) 机制在做（六.三.二），不是这个模块自己写循环。
def compute_backoff_seconds(attempt_number: int, *, base_seconds: float) -> float:
    """attempt_number is 1-indexed (the retry about to be made). base=0.5 -> 0.5, 1, 2, ..."""
    return base_seconds * (2 ** (attempt_number - 1))


def cancel_key(request_id: str) -> str:
    return f"cancel:{request_id}"


# 【卷 六.二.二】这个类存在的唯一原因，是把"一次同步的 Redis GET"包装成三.一.三提到的那个
# 【卷】Callable[[], bool] 签名——provider.chat() 每吐一个 token 就调用一次这个对象（不用 await），
# 【卷】问一句"我该停了吗"。取消的整个流程：客户端断线 → app/api/chat.py 检测到 → 在 Redis 里
# 【卷】写一个 cancel:{request_id} 标记 → worker 这边循环到下一个 token 时，这个 checker 读到
# 【卷】标记 → 停止生成。注意是"轮询式协作取消"，不是强行 kill 掉正在跑的任务——SEQUENCE_DIAGRAMS.md
# 【卷】第 2 节解释了为什么不用 Celery 的 task.revoke(terminate=True)：那个会给整个 worker 进程
# 【卷】发 SIGTERM，把同一个 worker 上其他正在跑的任务也一起杀掉，粒度太粗。
class CancellationChecker:
    """Sync Redis GET wrapped as the `Callable[[], bool]` the `ModelProvider` protocol requires
    (ARCHITECTURE.md §4) — the provider's async loop already yields control per-token, so this
    stays a cheap synchronous poll rather than a new concurrency primitive (SEQUENCE_DIAGRAMS.md
    §2)."""

    def __init__(self, redis_client: SyncRedis, request_id: str) -> None:
        self._redis = redis_client
        self._key = cancel_key(request_id)

    def __call__(self) -> bool:
        return self._redis.get(self._key) is not None
