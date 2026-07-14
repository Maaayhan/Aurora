from providers.mock_provider import MockProvider
from shared.schemas import ChatMessage

USER_HI = [ChatMessage(role="user", content="hi")]


async def test_streams_response_word_by_word_then_a_finished_chunk():
    provider = MockProvider(response="one two three")
    chunks = [c async for c in provider.chat(USER_HI, cancel_check=lambda: False)]
    assert "".join(c.content for c in chunks) == "one two three"
    assert chunks[-1].finished is True
    assert chunks[-1].completion_tokens == 3


async def test_cancel_check_stops_the_stream_early():
    provider = MockProvider(response="one two three four five")
    seen = 0

    def cancel_after_two():
        nonlocal seen
        return seen >= 2

    chunks = []
    async for c in provider.chat(USER_HI, cancel_check=cancel_after_two):
        chunks.append(c)
        seen += 1
    assert len(chunks) <= 3  # stopped well before all 5 words + finish chunk


async def test_fail_after_chunks_raises_mid_stream():
    provider = MockProvider(response="one two three", fail_after_chunks=1)
    collected = []
    raised = False
    try:
        async for c in provider.chat(USER_HI, cancel_check=lambda: False):
            collected.append(c)
    except ConnectionError:
        raised = True
    assert raised
    assert len(collected) == 1


async def test_health_check_reports_configured_state():
    healthy = await MockProvider(healthy=True).health_check()
    assert healthy.healthy is True
    unhealthy = await MockProvider(healthy=False).health_check()
    assert unhealthy.healthy is False
