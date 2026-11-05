"""Configurable retention and disk thresholds for disposable caches.

Optional local overrides live at ``data/logs/storage/storage_policy.local.json``.
Missing keys keep the defaults below. RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from research.sports.core import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "data" / "logs" / "storage"
CONFIG_PATH = CONFIG_DIR / "storage_policy.local.json"
STATUS_PATH = CONFIG_DIR / "disk_health.json"
GROWTH_HISTORY_PATH = CONFIG_DIR / "disk_usage_history.jsonl"

CONFIRM_TOKEN = "DELETE"


@dataclass
class CacheLimit:
    """Retention for one reconstructable HTTP-cache root (repo-relative POSIX prefix)."""

    prefix: str
    max_age_days: float = 14.0
    max_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GiB soft cap per root


@dataclass
class StorageConfig:
    free_warning_bytes: int = 10 * 1024 * 1024 * 1024   # 10 GiB
    free_critical_bytes: int = 2 * 1024 * 1024 * 1024    # 2 GiB
    growth_lookback_hours: float = 24.0
    refuse_cleanup_when_collectors_busy: bool = True
    # Global regenerable-cache budget (immutable evidence excluded).
    max_cache_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB
    default_cache_retention_days: float = 7.0
    cache_limits: list[CacheLimit] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cache_limits:
            d = self.default_cache_retention_days
            # Soft per-root caps; cleanup also enforces max_cache_bytes globally.
            self.cache_limits = [
                CacheLimit("data/cache/sports/raw/", max_age_days=d, max_bytes=3 * 1024**3),
                CacheLimit("data/cache/sports/raw_capture_v2_dev/", max_age_days=d, max_bytes=1 * 1024**3),
                CacheLimit("data/cache/sports/kalshi_discovery/", max_age_days=d, max_bytes=512 * 1024**2),
                CacheLimit("data/cache/weather/", max_age_days=d, max_bytes=2 * 1024**3),
                CacheLimit("data/cache/inventory/", max_age_days=d, max_bytes=3 * 1024**3),
                CacheLimit("data/cache/events/", max_age_days=d, max_bytes=512 * 1024**2),
                CacheLimit("data/cache/series/", max_age_days=d, max_bytes=256 * 1024**2),
                CacheLimit("data/cache/candles/", max_age_days=d, max_bytes=1 * 1024**3),
                CacheLimit("data/cache/fees/", max_age_days=d, max_bytes=256 * 1024**2),
                CacheLimit("data/cache/logs/", max_age_days=d, max_bytes=256 * 1024**2),
            ]


def _limit_from_dict(d: dict) -> CacheLimit:
    return CacheLimit(
        prefix=str(d["prefix"]).replace("\\", "/"),
        max_age_days=float(d.get("max_age_days", 14.0)),
        max_bytes=int(d.get("max_bytes", 20 * 1024**3)),
    )


def load_config(path: Path | None = None, root: Path = REPO_ROOT) -> StorageConfig:
    cfg = StorageConfig()
    # Env overrides (GB → bytes for max cache). Applied before local JSON so the
    # file can still override when operators want a pinned local policy.
    if os.environ.get("KALSHI_STORAGE_MAX_CACHE_GB"):
        cfg.max_cache_bytes = int(float(os.environ["KALSHI_STORAGE_MAX_CACHE_GB"]) * 1024**3)
    if os.environ.get("KALSHI_STORAGE_CACHE_RETENTION_DAYS"):
        cfg.default_cache_retention_days = float(os.environ["KALSHI_STORAGE_CACHE_RETENTION_DAYS"])
        for lim in cfg.cache_limits:
            lim.max_age_days = cfg.default_cache_retention_days
    p = path or (root / "data" / "logs" / "storage" / "storage_policy.local.json")
    if not p.is_file():
        return cfg
    raw = json.loads(p.read_text(encoding="utf-8"))
    if "free_warning_bytes" in raw:
        cfg.free_warning_bytes = int(raw["free_warning_bytes"])
    if "free_critical_bytes" in raw:
        cfg.free_critical_bytes = int(raw["free_critical_bytes"])
    if "growth_lookback_hours" in raw:
        cfg.growth_lookback_hours = float(raw["growth_lookback_hours"])
    if "refuse_cleanup_when_collectors_busy" in raw:
        cfg.refuse_cleanup_when_collectors_busy = bool(raw["refuse_cleanup_when_collectors_busy"])
    if "max_cache_bytes" in raw:
        cfg.max_cache_bytes = int(raw["max_cache_bytes"])
    if "default_cache_retention_days" in raw:
        cfg.default_cache_retention_days = float(raw["default_cache_retention_days"])
    if "cache_limits" in raw:
        cfg.cache_limits = [_limit_from_dict(x) for x in raw["cache_limits"]]
    return cfg


def config_as_dict(cfg: StorageConfig) -> dict:
    return {
        "schema": "kel_storage_policy_v1",
        "research_only": True,
        "no_bet": True,
        "free_warning_bytes": cfg.free_warning_bytes,
        "free_critical_bytes": cfg.free_critical_bytes,
        "growth_lookback_hours": cfg.growth_lookback_hours,
        "refuse_cleanup_when_collectors_busy": cfg.refuse_cleanup_when_collectors_busy,
        "max_cache_bytes": cfg.max_cache_bytes,
        "default_cache_retention_days": cfg.default_cache_retention_days,
        "cache_limits": [asdict(x) for x in cfg.cache_limits],
        "confirm_token": CONFIRM_TOKEN,
        "note": "Immutable evidence is never auto-deleted. Cleanup defaults to dry-run. "
                "max_cache_bytes applies only to regenerable caches.",
    }
