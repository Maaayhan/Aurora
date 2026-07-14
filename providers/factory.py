"""Selects a ModelProvider implementation from config — the one place `MODEL_PROVIDER` is read.

`worker/` calls this once at process start; nothing else in the codebase
branches on which provider is configured (ARCHITECTURE.md §4).
"""

from providers.base import ModelProvider
from providers.mock_provider import MockProvider
from providers.ollama_provider import OllamaProvider


# 【卷 三.四】全项目唯一一处读取"用哪个 Provider"这个决策的地方。工厂模式的价值不在于代码量少，
# 【卷】而在于"分支判断"只出现这一次——如果每个用到 provider 的地方都自己 if/else 判断
# 【卷】MODEL_PROVIDER，未来加一个新实现（比如真的接了 vLLM）就要全项目搜索修改，容易漏改。
def build_provider(
    provider_name: str, *, provider_url: str, default_model: str, timeout_seconds: float
) -> ModelProvider:
    match provider_name:
        case "ollama":
            return OllamaProvider(
                base_url=provider_url, model=default_model, timeout_seconds=timeout_seconds
            )
        case "mock":
            return MockProvider()
        case "vllm":
            raise NotImplementedError(
                "VLLMProvider is designed (ARCHITECTURE.md §4) but not implemented — "
                "no GPU cluster available in this project's scope."
            )
        case _:
            raise ValueError(f"Unknown MODEL_PROVIDER: {provider_name!r}")
