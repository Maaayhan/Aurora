import fakeredis.aioredis
import pytest

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
