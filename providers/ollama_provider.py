"""Ollama-backed ModelProvider — local development stand-in for Aurora-13B, ARCHITECTURE.md §4.

Talks to Ollama's OpenAI-compatible `/v1/chat/completions` endpoint
(`stream: true`) over HTTP. This is the only implementation detail specific to
Ollama in the whole codebase — everything above `providers/` sees only
`ModelProvider`.
"""

import json
import time
from collections.abc import AsyncIterator, Callable

import httpx

from shared.schemas import ChatChunk, ChatMessage, ProviderHealth


class OllamaProvider:
    def __init__(self, base_url: str, model: str, *, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        self._timeout_seconds = timeout_seconds

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        cancel_check: Callable[[], bool],
    ) -> AsyncIterator[ChatChunk]:
        # `model` is fixed per provider instance for now — the task payload's `model`
        # field is recorded (DATA_MODEL.md §3) but per-request routing across models
        # is bonus scope (ROADMAP.md), not wired up until the provider boundary needs
        # to serve more than one model at a time.
        # 【卷 三.二.一】这里是 DEVLOG.md 问题 #12 的真实现场。一开始这里没有 stream_options
        # 【卷】这一行，代码看起来完全正常、单测也全过——因为 MockProvider 是自己造数据，
        # 【卷】不会漏掉 usage。直到接了真实 Ollama 才发现：不显式要求 include_usage，
        # 【卷】流式响应里根本不会带 token 数量，Postgres 里 prompt_tokens/completion_tokens
        # 【卷】会一直是 0。这就是"单元测试测不出第三方 API 真实行为"的典型例子——Mock 只能
        # 【卷】保证"你以为它长这样"，测不出"它实际长这样"。
        payload = {
            "model": self._default_model,
            "messages": [m.model_dump(by_alias=False) for m in messages],
            "stream": True,
            # Without this, Ollama's streamed chunks never carry a `usage` object at all — token
            # counts would silently stay 0 all the way through to `messages.prompt_tokens`/
            # `completion_tokens` in Postgres (DATA_MODEL.md §1). Confirmed empirically against a
            # live Ollama instance, not assumed from the OpenAI spec alone.
            "stream_options": {"include_usage": True},
        }
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout) as client:
            async with client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if cancel_check():
                        return
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or {}
                    choices = chunk.get("choices") or []
                    if not choices:
                        # 【卷 三.二.二】这里第二个坑：加了 include_usage 之后，usage 不是
                        # 【卷】"贴在最后一条内容消息上"，而是作为单独一帧、choices 是空数组
                        # 【卷】发过来的。改之前的代码写的是 `chunk["choices"][0]`，如果这一帧
                        # 【卷】真的被触发，会直接 IndexError 崩掉。这行 `if not choices` 判断
                        # 【卷】就是专门接住这种"usage-only、没有内容"的帧。
                        # The `include_usage` final chunk: empty `choices`, usage-only. Ollama
                        # (like OpenAI) sends this as a separate frame after the last content
                        # delta, not attached to the `finish_reason` chunk.
                        if usage:
                            yield ChatChunk(
                                content="",
                                finished=True,
                                prompt_tokens=usage.get("prompt_tokens"),
                                completion_tokens=usage.get("completion_tokens"),
                            )
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = choice.get("finish_reason")
                    if content or finish_reason:
                        yield ChatChunk(
                            content=content,
                            finished=finish_reason is not None,
                            prompt_tokens=usage.get("prompt_tokens"),
                            completion_tokens=usage.get("completion_tokens"),
                        )

    async def health_check(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0) as client:
                response = await client.get("/v1/models")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ProviderHealth(healthy=False, error=str(exc))
        latency_ms = (time.monotonic() - start) * 1000
        return ProviderHealth(healthy=True, latency_ms=latency_ms)
