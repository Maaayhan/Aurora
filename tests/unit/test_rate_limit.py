import asyncio
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.core import rate_limit
from app.core.rate_limit import check_rate_limit


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_allows_requests_under_the_limit(redis):
    for _ in range(3):
        result = await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)
        assert result.allowed is True


async def test_rejects_the_request_that_would_exceed_the_limit(redis):
    for _ in range(3):
        await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)
    result = await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)
    assert result.allowed is False
    assert result.retry_after_seconds > 0


async def test_rejected_request_is_not_counted_towards_the_window(redis):
    """A client that's over quota should never add load — ERROR_HANDLING.md §1."""
    for _ in range(3):
        await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)
    await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)  # rejected
    count = await redis.zcard("ratelimit:user-1")
    assert count == 3


async def test_identities_are_isolated(redis):
    for _ in range(3):
        await check_rate_limit(redis, "user-1", limit=3, window_seconds=60)
    result = await check_rate_limit(redis, "user-2", limit=3, window_seconds=60)
    assert result.allowed is True


class _YieldingRedis:
    """Hands control back to the event loop before every command, as a network
    round trip does against a real Redis. fakeredis completes each command
    without yielding, which hides any gap between two separate commands."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        attr = getattr(self._inner, name)

        async def call(*args, **kwargs):
            await asyncio.sleep(0)
            return await attr(*args, **kwargs)

        return call


async def test_concurrent_requests_never_exceed_the_limit(redis):
    """The check and the add must be one atomic step: with a check-then-add
    sequence, every concurrent caller can observe `count < limit` before any of
    them has added its entry, and all of them are let through."""
    limit = 5
    client = _YieldingRedis(redis)
    results = await asyncio.gather(
        *(check_rate_limit(client, "user-1", limit=limit, window_seconds=60) for _ in range(30))
    )
    allowed = sum(1 for r in results if r.allowed)
    assert allowed == limit
    assert await redis.zcard("ratelimit:user-1") == limit


async def test_retry_after_rounds_up_so_a_waiting_client_is_not_rejected_again(redis, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: clock["now"]))

    await check_rate_limit(redis, "user-1", limit=1, window_seconds=60)  # recorded at t=1000.0
    clock["now"] = 1000.4  # 59.6 s left in the window
    result = await check_rate_limit(redis, "user-1", limit=1, window_seconds=60)

    assert result.allowed is False
    assert result.retry_after_seconds == 60  # int() would say 59, and 59 s is still too early
