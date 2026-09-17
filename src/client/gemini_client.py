"""Google Gemini concrete vision provider.

A multimodal client used for the fast OCR path (one call per whole page:
transcription + diagram localization). Programs against the contracts in
``base.py``; feature modules should obtain it through
``src.client.get_vision_client`` rather than importing this file directly.

Requires the unified ``google-genai`` SDK and an AI Studio API key
(``GEMINI_API_KEY``). The SDK is imported lazily so this module can be imported
without the dependency installed.
"""

from __future__ import annotations

from typing import Any, Optional

from src.config import settings
from src.logging.logger import get_logger

from .base import BaseLLMClient, BaseVisionClient, LLMResponse

logger = get_logger(__name__)




class GeminiLLMClient(BaseLLMClient):
    """Gemini-backed text-only LLM client for evaluation and feedback."""

    def __init__(self, model=None, client=None):
        self._model = model or settings.GEMINI_OCR_MODEL
        self._client = client
        self.validate_config()

    def validate_config(self):
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. Export it (AI Studio key) to use the "
                "Gemini provider."
            )

    def _ensure_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
            logger.info("Gemini LLM client initialized (model=%s)", self._model)
        return self._client

    def complete(self, system_prompt, user_message):
        from google.genai import types
        client = self._ensure_client()
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=0.0,
        )
        response = client.models.generate_content(
            model=self._model,
            contents=[user_message],
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        tokens = getattr(usage, "total_token_count", 0) or 0
        return LLMResponse(content=response.text or "", usage_tokens=tokens)

class GeminiVisionClient(BaseVisionClient):
    """Gemini-backed multimodal client (image + text -> text)."""

    def __init__(self, model: Optional[str] = None, client: Any = None) -> None:
        self._model = model or settings.GEMINI_OCR_MODEL
        self._client = client  # injectable for tests
        self.validate_config()

    def validate_config(self) -> None:
        """Fail fast on missing API key so misconfig surfaces at startup."""
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. Export it (AI Studio key) to use the "
                "Gemini OCR/vision provider, or set OCR_ENGINE=surya for local OCR."
            )

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - env guard
                raise RuntimeError(
                    "google-genai is not installed. Run `pip install google-genai` "
                    "(it is in requirements.txt) to use the Gemini provider."
                ) from exc
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
            logger.info("Gemini client initialized (model=%s)", self._model)
        return self._client

    def complete_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image: bytes,
        *,
        mime_type: str = "image/png",
        response_json: bool = False,
    ) -> LLMResponse:
        """Send one image + prompt to Gemini and return the text response.

        ``response_json`` asks the model to emit strict JSON (used by the OCR
        path); the raw text is returned untouched for the caller to parse.
        """
        from google.genai import types

        client = self._ensure_client()
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=0.0,
            response_mime_type="application/json" if response_json else None,
        )
        response = client.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=image, mime_type=mime_type),
                user_message,
            ],
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        tokens = getattr(usage, "total_token_count", 0) or 0
        return LLMResponse(content=response.text or "", usage_tokens=tokens)
