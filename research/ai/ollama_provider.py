"""Local Ollama provider (``/api/chat``, non-streaming, JSON-schema constrained output)."""

from __future__ import annotations

import time
from typing import Any

import requests

from .models import AIResponse
from .provider import MalformedProviderResponse, ModelMissing, ProviderError, ProviderTimeout, ProviderUnavailable

STATUS_TIMEOUT_S = 3.0


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, *, timeout_s: float = 180.0, num_ctx: int = 8192,
                 max_output_tokens: int = 1500, session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self.max_output_tokens = max_output_tokens
        self.session = session or requests.Session()

    def _unreachable(self, error: Exception) -> ProviderUnavailable:
        return ProviderUnavailable(
            f"Cannot reach Ollama at {self.base_url} ({type(error).__name__}). Is Ollama installed and "
            "`ollama serve` running?", code="ollama_unreachable")

    def generate(self, prompt: str, *, system_prompt: str | None = None,
                 json_schema: dict[str, Any] | None = None) -> AIResponse:
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [
            {"role": "user", "content": prompt}]
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "think": False,
                                "options": {"temperature": 0.2, "num_ctx": self.num_ctx,
                                            "num_predict": self.max_output_tokens}}
        if json_schema is not None:
            body["format"] = json_schema
        started = time.monotonic()
        resp = self._post(body)
        if resp.status_code == 400 and "think" in resp.text.lower():
            body.pop("think")  # models without a thinking toggle reject the field
            resp = self._post(body)
        if resp.status_code == 404:
            raise ModelMissing(f"Ollama model '{self.model}' is not installed. Run: ollama pull {self.model}")
        if resp.status_code != 200:
            raise ProviderError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}",
                                code="provider_http_error")
        try:
            payload = resp.json()
            text = payload["message"]["content"]
        except (ValueError, KeyError, TypeError) as error:
            raise MalformedProviderResponse(f"Unexpected Ollama response shape: {error}") from error
        if not isinstance(text, str):
            raise MalformedProviderResponse("Ollama message content is not text")
        if payload.get("done_reason") == "length":
            raise MalformedProviderResponse(
                f"Ollama stopped at the {self.max_output_tokens}-token output limit before finishing the JSON "
                "(raise AI_MAX_OUTPUT_TOKENS or use a model that follows the length limits)")
        return AIResponse(text=text, provider=self.name, model=self.model,
                          duration_s=round(time.monotonic() - started, 2),
                          meta={k: payload.get(k) for k in ("total_duration", "eval_count", "prompt_eval_count")})

    def _post(self, body: dict[str, Any]) -> requests.Response:
        try:
            return self.session.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout_s)
        except requests.Timeout as error:
            raise ProviderTimeout(f"Ollama did not answer within {self.timeout_s:.0f} s") from error
        except requests.RequestException as error:
            raise self._unreachable(error) from error

    def status(self) -> dict[str, Any]:
        base = {"provider": self.name, "model": self.model, "base_url": self.base_url}
        try:
            resp = self.session.get(f"{self.base_url}/api/tags", timeout=STATUS_TIMEOUT_S)
            names = [m.get("name") for m in (resp.json().get("models") or [])] if resp.status_code == 200 else []
        except (requests.RequestException, ValueError, AttributeError) as error:
            err = self._unreachable(error)
            return {**base, "available": False, "code": err.code, "message": str(err)}
        if resp.status_code != 200:
            return {**base, "available": False, "code": "provider_http_error",
                    "message": f"Ollama returned HTTP {resp.status_code}"}
        wanted = {self.model, f"{self.model}:latest"}
        if not wanted & set(names):
            return {**base, "available": False, "code": "model_missing",
                    "message": f"Model '{self.model}' not installed. Run: ollama pull {self.model}",
                    "installed_models": names}
        return {**base, "available": True, "code": "ok", "message": "Ollama reachable; model installed."}
