"""Provider-neutral AI interface. Provider-specific code lives in its own module."""

from __future__ import annotations

from typing import Any, Protocol

from .config import AISettings
from .models import AIResponse


class ProviderError(Exception):
    """Base provider failure. ``code`` is stable and shown to users."""

    code = "provider_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class ProviderTimeout(ProviderError):
    code = "provider_timeout"


class ModelMissing(ProviderError):
    code = "model_missing"


class MalformedProviderResponse(ProviderError):
    code = "malformed_response"


class AIProvider(Protocol):
    name: str
    model: str | None

    def generate(self, prompt: str, *, system_prompt: str | None = None,
                 json_schema: dict[str, Any] | None = None) -> AIResponse: ...

    def status(self) -> dict[str, Any]: ...


class NullProvider:
    """``AI_PROVIDER=none``: AI disabled; every call reports unavailable."""

    name = "none"
    model = None

    def generate(self, prompt: str, *, system_prompt: str | None = None,
                 json_schema: dict[str, Any] | None = None) -> AIResponse:
        raise ProviderUnavailable("AI research is disabled (AI_PROVIDER=none)", code="ai_disabled")

    def status(self) -> dict[str, Any]:
        return {"provider": "none", "model": None, "available": False, "code": "ai_disabled",
                "message": "AI research is disabled (AI_PROVIDER=none)."}


def get_provider(settings: AISettings) -> AIProvider:
    if settings.provider == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider(settings.ollama_base_url, settings.ollama_model, timeout_s=settings.timeout_s,
                              num_ctx=settings.num_ctx, max_output_tokens=settings.max_output_tokens)
    return NullProvider()
