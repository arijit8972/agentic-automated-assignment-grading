"""Azure OpenAI concrete provider — self-registering.

Both text and vision completions use the same Azure deployment. A GPT-4o /
GPT-4.1 deployment is multimodal, so the same endpoint grades text answers
and diagram image crops.

Programs against the contracts in ``base.py`` only.  Feature modules obtain
clients through ``src.client.get_llm_client`` / ``src.client.get_vision_client``.
"""

from __future__ import annotations

import base64
from typing import Any

from src.config import settings
from src.logging.logger import get_logger

from .base import BaseLLMClient, BaseVisionClient, LLMResponse

logger = get_logger(__name__)


def _require(name: str, value: str) -> None:
    """Raise ``ValueError`` if *value* is empty (fast-fail config check)."""
    if not value:
        raise ValueError(
            f"{name} is not set. Export it or add it to your .env to use the "
            "Azure OpenAI provider."
        )


class AzureLLMClient(BaseLLMClient):
    """Azure OpenAI chat-completion client (text only)."""

    def __init__(self, client: Any = None) -> None:
        self._client = client  # injectable for tests
        self.validate_config()

    def validate_config(self) -> None:
        _require("AZURE_OPENAI_API_KEY", settings.AZURE_OPENAI_API_KEY)
        _require("AZURE_OPENAI_ENDPOINT", settings.AZURE_OPENAI_ENDPOINT)

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            logger.info(
                "Azure LLM client initialized (deployment=%s)",
                settings.AZURE_OPENAI_DEPLOYMENT,
            )
        return self._client

    def complete(self, system_prompt: str, user_message: str) -> LLMResponse:
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        tokens = getattr(response.usage, "total_tokens", 0) or 0
        return LLMResponse(content=content, usage_tokens=tokens)


def _to_jpeg(image: bytes, quality: int = 90) -> bytes:
    """Convert image bytes to JPEG regardless of source format.

    Azure OpenAI vision is most reliable with JPEG input — PNG works in theory
    but triggers ``invalid_image_format`` 400 errors on some GPT-4.1 deployments.
    Quality 90 preserves diagram detail at a fraction of the PNG size.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


class AzureVisionClient(BaseVisionClient):
    """Azure OpenAI vision client (image + text → text).

    Sends an image as a base64 data URL alongside the prompt.  Requires a
    vision-capable deployment (GPT-4o, GPT-4.1, etc.).
    """

    def __init__(self, client: Any = None) -> None:
        self._client = client  # injectable for tests
        self.validate_config()

    def validate_config(self) -> None:
        _require("AZURE_OPENAI_API_KEY", settings.AZURE_OPENAI_API_KEY)
        _require("AZURE_OPENAI_ENDPOINT", settings.AZURE_OPENAI_ENDPOINT)

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            logger.info(
                "Azure vision client initialized (deployment=%s)",
                settings.AZURE_OPENAI_DEPLOYMENT,
            )
        return self._client

    def complete_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image: bytes,
        mime_type: str = "image/png",
        response_json: bool = False,
    ) -> LLMResponse:
        client = self._ensure_client()
        # Azure vision is most reliable with JPEG: convert regardless of source
        # format (avoids invalid_image_format 400s with PNG on some deployments).
        jpeg_bytes = _to_jpeg(image)
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        extra: dict[str, Any] = {}
        if response_json:
            extra["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                # "detail" omitted — not supported on all GPT-4.1
                                # deployments and causes confusing format errors.
                                "url": f"data:image/jpeg;base64,{b64}",
                            },
                        },
                        {"type": "text", "text": user_message},
                    ],
                },
            ],
            temperature=0.0,
            **extra,
        )
        content = response.choices[0].message.content or ""
        tokens = getattr(response.usage, "total_tokens", 0) or 0
        return LLMResponse(content=content, usage_tokens=tokens)
