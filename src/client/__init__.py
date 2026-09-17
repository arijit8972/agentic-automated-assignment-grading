"""Provider-agnostic client package.

Feature modules import the abstract contracts and the factory helpers below —
never a concrete provider module (``azure_client`` / ``gemini_client``) directly.
"""

from __future__ import annotations

from .base import BaseLLMClient, BaseVisionClient, LLMResponse

__all__ = [
    "BaseLLMClient",
    "BaseVisionClient",
    "LLMResponse",
    "get_llm_client",
    "get_vision_client",
]


def get_llm_client(provider: str) -> BaseLLMClient:
    """Return a concrete LLM client for *provider* (e.g. "gemini", "azure").

    Concrete providers are imported lazily so their SDKs are only required when
    that provider is actually selected.
    """
    key = provider.strip().lower()
    if key == "gemini":
        from .gemini_client import GeminiLLMClient

        return GeminiLLMClient()
    if key == "azure":
        from .azure_client import AzureLLMClient

        return AzureLLMClient()
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def get_vision_client(provider: str) -> BaseVisionClient:
    """Return a concrete vision client for *provider* (e.g. "gemini", "azure").

    Concrete providers are imported lazily so their SDKs are only required when
    that provider is actually selected.
    """
    key = provider.strip().lower()
    if key == "gemini":
        from .gemini_client import GeminiVisionClient

        return GeminiVisionClient()
    if key == "azure":
        from .azure_client import AzureVisionClient

        return AzureVisionClient()
    raise ValueError(f"Unknown vision provider: {provider!r}")
