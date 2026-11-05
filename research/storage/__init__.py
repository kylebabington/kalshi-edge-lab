"""Disk-space protection and storage classification for Kalshi Edge Lab.

RESEARCH_ONLY / NO_BET. Never automatically deletes immutable research evidence.
Does not change frozen models, protocols, predictions, or evaluation methods.
"""

from .config import StorageConfig, load_config
from .health import DiskHealth, assess_disk
from .policy import CLASS_IMMUTABLE, CLASS_APPEND_ONLY, CLASS_HTTP_CACHE, CLASS_TEMP, CLASS_REPORT, classify
from .roots import ArchiveStatus, StorageRoots, archive_status, load_roots

__all__ = [
    "StorageConfig",
    "load_config",
    "DiskHealth",
    "assess_disk",
    "CLASS_IMMUTABLE",
    "CLASS_APPEND_ONLY",
    "CLASS_HTTP_CACHE",
    "CLASS_TEMP",
    "CLASS_REPORT",
    "classify",
    "StorageRoots",
    "ArchiveStatus",
    "load_roots",
    "archive_status",
]
