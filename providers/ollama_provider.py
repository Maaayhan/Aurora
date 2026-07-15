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
        # A single float applied to httpx's connect/read/write/pool phases (ERROR_HANDLING.md
        # §2's "per-attempt" timeout): a fresh AsyncClient — and therefore a fresh deadline — is
        # constructed on every call to `chat()`, so each retry attempt gets its own independent
        # window rather than sharing one deadline across the whole task. httpx's read timeout is
        # an *idle* timeout (resets on every chunk received, not a hard wall-clock cap on the
        # whole stream) — matching ERROR_HANDLING.md §2's "if no data arrives in time" wording,
        # which describes the same idle-timeout semantics `asyncio.wait_for` would give, without
        # needing a second timeout layered on top of httpx's own.
        timeout = httpx.Timeout(self._timeout_seconds)
        try:
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
                            # The `include_usage` final chunk: empty `choices`, usage-only.
                            # Ollama (like OpenAI) sends this as a separate frame after the last
                            # content delta, not attached to the `finish_reason` chunk.
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
        except httpx.TimeoutException as exc:
            # Re-raised with a guaranteed "timed out" message: httpx doesn't promise its own
            # exception message mentions "timeout" (it can be empty depending on which phase
            # fired), and worker/tasks.py:_classify_error tells INFERENCE_TIMEOUT apart from
            # INFERENCE_ERROR by string-matching the message — string-matching an unguaranteed
            # message would silently misclassify a real timeout as a generic inference error.
            raise TimeoutError(
                f"Model provider timed out after {self._timeout_seconds}s with no data"
            ) from exc

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
