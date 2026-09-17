"""Provider-agnostic LLM/vision client abstraction.

Feature modules program against this base — never import a concrete
provider (Azure/local) directly.
"""

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel


class LLMResponse(BaseModel):
    content: str
    usage_tokens: int = 0


class BaseLLMClient(ABC):
    """Abstract LLM client contract."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_message: str,
    ) -> LLMResponse:
        """Send a text completion request."""
        ...

    @abstractmethod
    def validate_config(self) -> None:
        """Validate client configuration at startup. Fail fast on bad config."""
        ...


class BaseVisionClient(ABC):
    """Abstract vision client contract."""

    @abstractmethod
    def complete_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image: bytes,
    ) -> LLMResponse:
        """Send a vision completion request with an image."""
        ...

    @abstractmethod
    def validate_config(self) -> None:
        """Validate client configuration at startup. Fail fast on bad config."""
        ...
