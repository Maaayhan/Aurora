"""Deterministic ModelProvider for tests — ARCHITECTURE.md §4.

Lets worker/ tests (and app/ tests that exercise the full chat flow) run
without a live Ollama instance. Also used to deterministically exercise
failure paths (timeout, error) that would be flaky to trigger against a real
model.
"""

import asyncio
from collections.abc import AsyncIterator, Callable

from shared.schemas import ChatChunk, ChatMessage, ProviderHealth


# 【卷 三.三】留意 fail_after_chunks 这个参数：它能精确控制"在发出第几个 token 之后失败"。
# 【卷】这是专门为了测试 worker/tasks.py 里那条"重试只在还没发出任何 token 之前才安全"的
# 【卷】规则而设计的——
# 【卷】fail_after_chunks=0 模拟"一个 token 都没发就失败"（应该重试），fail_after_chunks=1 模拟
# 【卷】"发了一个 token 之后才失败"（不能重试，只能报终态错误）。测试文件里能看到两种场景都覆盖了。
class MockProvider:
    def __init__(
        self,
        *,
        response: str = "Hello from MockProvider.",
        chunk_delay_seconds: float = 0.0,
        fail_after_chunks: int | None = None,
        healthy: bool = True,
    ) -> None:
        self._response = response
        self._chunk_delay_seconds = chunk_delay_seconds
        self._fail_after_chunks = fail_after_chunks
        self._healthy = healthy

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        cancel_check: Callable[[], bool],
    ) -> AsyncIterator[ChatChunk]:
        words = self._response.split(" ")
        for i, word in enumerate(words):
            if cancel_check():
                return
            if self._fail_after_chunks is not None and i >= self._fail_after_chunks:
                raise ConnectionError("MockProvider: simulated inference failure")
            if self._chunk_delay_seconds:
                await asyncio.sleep(self._chunk_delay_seconds)
            content = word if i == 0 else f" {word}"
            yield ChatChunk(content=content, finished=False)
        yield ChatChunk(
            content="",
            finished=True,
            prompt_tokens=sum(len(m.content.split()) for m in messages),
            completion_tokens=len(words),
        )

    async def health_check(self) -> ProviderHealth:
        if self._healthy:
            return ProviderHealth(healthy=True, latency_ms=0.1)
        return ProviderHealth(healthy=False, error="MockProvider configured unhealthy")
