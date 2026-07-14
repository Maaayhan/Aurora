"""Selects a ModelProvider implementation from config — the one place `MODEL_PROVIDER` is read.

`worker/` calls this once at process start; nothing else in the codebase
branches on which provider is configured (ARCHITECTURE.md §4).
"""

from providers.base import ModelProvider
from providers.mock_provider import MockProvider
from providers.ollama_provider import OllamaProvider


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
