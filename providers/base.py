"""The only thing worker/ is allowed to depend on for talking to a model — ARCHITECTURE.md §4.

Nothing above this interface (queue logic, session logic, the API layer) knows
or cares whether the backing runtime is Ollama, vLLM, or a mock. Swapping
providers is a `MODEL_PROVIDER` config change, not a rewrite.
"""

from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable

from shared.schemas import ChatChunk, ChatMessage, ProviderHealth


@runtime_checkable
class ModelProvider(Protocol):
    # Deliberately `def`, not `async def`: implementations are async *generator* functions
    # (`async def chat(...): ... yield ...`), which return an AsyncIterator synchronously when
    # called — they are not coroutines and calling one is never itself awaited. Typing this as
    # `async def` (as ARCHITECTURE.md's illustrative pseudocode does) would tell a type checker
    # to expect `await provider.chat(...)`, which is wrong for every real implementation.
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        cancel_check: Callable[[], bool],
    ) -> AsyncIterator[ChatChunk]:
        """Stream a completion for `messages`.

        `cancel_check` is polled between chunks (ARCHITECTURE.md §2,
        SEQUENCE_DIAGRAMS.md §2) — implementations must check it every loop
        iteration and stop yielding (raising `asyncio.CancelledError` or simply
        returning) once it reports True, rather than only at the start.
        """
        ...

    async def health_check(self) -> ProviderHealth:
        """Cheap liveness probe used by `GET /health` (API_SPEC.md) — must not block on a full
        generation."""
        ...
