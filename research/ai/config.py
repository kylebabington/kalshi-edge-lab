"""AI settings from environment variables (all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kalshi import cache

PROVIDERS = ("ollama", "none")
AI_ENV_KEYS = ("AI_", "OLLAMA_", "OPPORTUNITY_")


def load_env_file(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines for AI/opportunity settings from the repo ``.env`` (gitignored).
    Variables already set in the process environment win. Missing file is fine."""
    path = path or (cache.REPO_ROOT / ".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        if key.startswith(AI_ENV_KEYS) and key not in os.environ:
            os.environ[key] = value.strip("'\"")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AISettings:
    provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    timeout_s: float = 180.0
    # Ollama defaults to a 4096-token context and silently drops the start of longer prompts.
    num_ctx: int = 8192
    max_output_tokens: int = 1500
    min_edge: float = 0.05
    research_max_age_s: float = 6 * 3600
    auto_research: bool = False
    auto_top_n: int = 5
    cache_dir: Path = cache.CACHE_ROOT / "ai_research"

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    @property
    def model(self) -> str | None:
        return self.ollama_model if self.provider == "ollama" else None

    @classmethod
    def from_env(cls) -> "AISettings":
        d = cls()
        provider = (os.environ.get("AI_PROVIDER") or d.provider).strip().lower()
        cache_dir = os.environ.get("AI_CACHE_DIR")
        return cls(
            provider=provider if provider in PROVIDERS else "none",
            ollama_base_url=(os.environ.get("OLLAMA_BASE_URL") or d.ollama_base_url).rstrip("/"),
            ollama_model=os.environ.get("OLLAMA_MODEL") or d.ollama_model,
            timeout_s=_float("AI_TIMEOUT_S", d.timeout_s),
            num_ctx=_int("AI_NUM_CTX", d.num_ctx),
            max_output_tokens=_int("AI_MAX_OUTPUT_TOKENS", d.max_output_tokens),
            min_edge=_float("AI_MIN_EDGE", d.min_edge),
            research_max_age_s=_float("AI_RESEARCH_MAX_AGE_S", d.research_max_age_s),
            auto_research=_bool("AI_AUTO_RESEARCH", d.auto_research),
            auto_top_n=_int("AI_AUTO_TOP_N", d.auto_top_n),
            cache_dir=Path(cache_dir) if cache_dir else d.cache_dir,
        )
