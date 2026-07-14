"""The only thing worker/ is allowed to depend on for talking to a model — ARCHITECTURE.md §4.

Nothing above this interface (queue logic, session logic, the API layer) knows
or cares whether the backing runtime is Ollama, vLLM, or a mock. Swapping
providers is a `MODEL_PROVIDER` config change, not a rewrite.
"""

from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable

from shared.schemas import ChatChunk, ChatMessage, ProviderHealth


# 【卷 三.一.一】这是全项目"可插拔架构"的核心：ModelProvider 只有两个方法，worker/ 只允许
# 【卷】依赖这个接口，不允许直接依赖 OllamaProvider 或任何具体实现。这样"本地用 Ollama，
# 【卷】未来生产环境换成 vLLM"就只是改一个环境变量（见 providers/factory.py），不需要动
# 【卷】worker/ 或 app/ 里的任何一行代码。
# 【卷】这是典型的"依赖倒置"：上层（worker）依赖抽象（Protocol），不依赖具体实现。
@runtime_checkable
class ModelProvider(Protocol):
    # 【卷 三.一.二】这里是一个真实的 mypy 踩坑：故意写成 def 而不是 async def。
    # 【卷】原因：chat 的具体实现（下面 ollama_provider.py / mock_provider.py）内部用了 yield，
    # 【卷】这种函数叫"异步生成器函数"——调用它本身是同步、立即返回的（返回一个异步迭代器对象），
    # 【卷】真正的 await 发生在后面 `async for chunk in provider.chat(...)` 的每一次取值上，
    # 【卷】而不是调用 chat(...) 这一步。如果这里写成 async def，类型检查器会认为调用 chat(...)
    # 【卷】本身需要 await，那就跟实际用法对不上了——这是写 Protocol 包一个异步生成器时的通用陷阱。
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
        # 【卷 三.一.三】cancel_check 的类型是 Callable[[], bool]——一个普通的同步函数，不是 async。
        # 【卷】跟上面 chat 方法的类型陷阱是同一类问题：worker/reliability.py 里的
        # 【卷】CancellationChecker 就是把一次同步的 Redis GET 包装成这个签名，
        # 【卷】因为调用方（这里的 chat 循环）每轮都要能"不用 await 就问一下要不要停"。
        ...

    async def health_check(self) -> ProviderHealth:
        """Cheap liveness probe used by `GET /health` (API_SPEC.md) — must not block on a full
        generation."""
        ...
