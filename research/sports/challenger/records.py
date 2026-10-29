"""Crash-safe write-once prediction, exclusion and settlement records.

Exclusive creation alone can leave a truncated JSON file. Publication is: write a complete
temporary file, fsync, then an atomic no-overwrite link (O_EXCL fallback). Incomplete or
checksum-mismatched destinations are detected and never overwritten on retry.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..core import sha256_json
from .constants import CAPTURE_DIR


SCHEMA_PREDICTION = "sports_itf_singles_challenger_capture_prediction"
SCHEMA_EXCLUSION = "sports_itf_singles_challenger_capture_exclusion"
SCHEMA_SETTLEMENT = "sports_itf_singles_challenger_capture_v1_settlement"
SCHEMA_OBSERVATION = "sports_itf_singles_challenger_capture_v2_observation"


class RecordExists(RuntimeError):
    pass


class IncompleteRecord(RuntimeError):
    def __init__(self, path: Path, status: str):
        super().__init__(f"{path}: existing file is {status}; refusing overwrite")
        self.path = path
        self.status = status


def safe(cluster: str) -> str:
    return cluster.replace(":", "_").replace("/", "_")


def prediction_path(comp_key: str, cluster: str) -> Path:
    return CAPTURE_DIR / "predictions" / comp_key / f"{safe(cluster)}.json"


def exclusion_path(comp_key: str, cluster: str, run_id: str) -> Path:
    return CAPTURE_DIR / "exclusions" / run_id / f"{comp_key}__{safe(cluster)}.json"


def settlement_path(comp_key: str, cluster: str) -> Path:
    """Legacy v1 write-once settlement file. Never overwritten by v2."""
    return CAPTURE_DIR / "settlements" / comp_key / f"{safe(cluster)}.json"


def observation_path(comp_key: str, cluster: str, attempt_id: str) -> Path:
    return (CAPTURE_DIR / "settlement_observations" / comp_key / safe(cluster)
            / f"{safe(attempt_id)}.json")


def dumps(rec: dict) -> bytes:
    return (json.dumps(rec, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def seal(rec: dict) -> dict:
    rec = json.loads(json.dumps(rec, default=str))
    rec["record_sha256"] = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    return rec


def inspect_record(path: Path) -> tuple[dict | None, str]:
    """Return (object, status). status is missing | incomplete | checksum_mismatch | ok."""
    if not path.exists():
        return None, "missing"
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "incomplete"
    if not raw.strip():
        return None, "incomplete"
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "incomplete"
    if not isinstance(obj, dict) or "record_sha256" not in obj:
        return None, "incomplete"
    got = sha256_json({k: v for k, v in obj.items() if k != "record_sha256"})
    if got != obj["record_sha256"]:
        return obj, "checksum_mismatch"
    return obj, "ok"


def _raise_existing(path: Path) -> None:
    rec, status = inspect_record(path)
    if status == "ok":
        raise RecordExists(str(path))
    raise IncompleteRecord(path, status)


def publish_exclusive(path: Path, rec: dict) -> dict:
    """Seal and publish ``rec``. Never replaces an existing destination."""
    sealed = seal(rec)
    data = dumps(sealed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _raise_existing(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            _raise_existing(path)
        except OSError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            try:
                dfd = os.open(path, flags)
            except FileExistsError:
                _raise_existing(path)
            try:
                written = 0
                while written < len(data):
                    written += os.write(dfd, data[written:])
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    rec2, status = inspect_record(path)
    if status != "ok":
        raise IncompleteRecord(path, status)
    return rec2


def load_complete(path: Path) -> tuple[dict | None, str]:
    return inspect_record(path)


def list_complete(root: Path) -> list[tuple[Path, dict]]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("**/*.json")):
        rec, status = inspect_record(p)
        if status == "ok":
            out.append((p, rec))
    return out
