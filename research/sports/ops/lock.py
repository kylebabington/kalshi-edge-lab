"""Cross-process lock for sports collector work (capture and settlement), manual or scheduled.

The lock file is created exclusively and records the owner PID and that process's start time. A lock
is removed only when its owner is confirmed gone (no such process, or the PID now belongs to a process
that started at a different time). Age alone never frees a lock. An unreadable lock (for example one
being written) is treated as held until it has been unreadable for ``unreadable_grace_s``.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..core import REPO_ROOT

LOG_DIR = REPO_ROOT / "data" / "logs" / "sports"
LOCK_PATH = LOG_DIR / "cycle.lock"
WEATHER_LOCK_PATH = REPO_ROOT / "data" / "logs" / "prospective" / "cycle.lock"
UNREADABLE_GRACE_S = 300


class LockBusy(RuntimeError):
    def __init__(self, message: str, owner: dict | None = None):
        super().__init__(message)
        self.owner = owner


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _windows_start_time(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 5:  # access denied: the process exists but belongs to someone else
            return "unknown"
        return None
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return "unknown"
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return _iso(datetime.fromtimestamp((ticks - 116444736000000000) / 1e7, timezone.utc))
    finally:
        k32.CloseHandle(handle)


def _posix_start_time(pid: int) -> str | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return "unknown"
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        fields = stat.read_text().rsplit(")", 1)[1].split()
        return f"ticks:{fields[19]}"
    return "unknown"


def process_start_time(pid: int) -> str | None:
    """Start time of a running process, ``"unknown"`` if it runs but cannot be inspected, None if gone."""
    if pid <= 0:
        return None
    return _windows_start_time(pid) if sys.platform == "win32" else _posix_start_time(pid)


def read_owner(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def owner_state(owner: dict | None) -> str:
    """``alive``, ``dead`` or ``unknown`` for a lock owner record."""
    if not owner or not isinstance(owner.get("pid"), int):
        return "unknown"
    started = process_start_time(owner["pid"])
    if started is None:
        return "dead"
    if started == "unknown" or started == owner.get("process_start_utc"):
        return "alive"
    return "dead"  # PID reused by a process that started at a different time


def lock_state(path: Path | None = None) -> dict:
    """Passive description of a lock file (never modifies it)."""
    path = path or LOCK_PATH
    if not path.exists():
        return {"held": False, "state": "free"}
    owner = read_owner(path)
    state = owner_state(owner)
    out = {"held": state != "dead", "state": state,
           "file_age_s": round(time.time() - path.stat().st_mtime, 1)}
    if owner:
        out["owner"] = {k: owner.get(k) for k in ("pid", "purpose", "acquired_utc", "host")}
    return out


def _try_create(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return True


def acquire(purpose: str, path: Path | None = None, log=None, unreadable_grace_s: float = UNREADABLE_GRACE_S) -> dict:
    path = path or LOCK_PATH
    payload = {"pid": os.getpid(), "process_start_utc": process_start_time(os.getpid()),
               "host": socket.gethostname(), "user": getpass.getuser(),
               "acquired_utc": _iso(datetime.now(timezone.utc)), "purpose": purpose}
    if _try_create(path, payload):
        return payload
    owner = read_owner(path)
    state = owner_state(owner)
    if state == "alive":
        raise LockBusy(f"sports lock held by pid {owner['pid']} ({owner.get('purpose')}, "
                       f"since {owner.get('acquired_utc')})", owner)
    if state == "unknown":
        age = time.time() - path.stat().st_mtime
        if age < unreadable_grace_s:
            raise LockBusy(f"sports lock unreadable for {age:.0f}s (possibly being written)", owner)
        if log:
            log(f"sports lock unreadable for {age:.0f}s with no identifiable owner; treating as stale")
    elif log:
        log(f"stale sports lock: owner pid {owner['pid']} ({owner.get('purpose')}) is not running; removing")
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if _try_create(path, payload):
        return payload
    raise LockBusy("sports lock taken by another process while clearing a stale lock", read_owner(path))


def release(payload: dict, path: Path | None = None) -> None:
    path = path or LOCK_PATH
    owner = read_owner(path)
    if owner and owner.get("pid") == payload["pid"] and owner.get("acquired_utc") == payload["acquired_utc"]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def sports_lock(purpose: str, path: Path | None = None, log=None):
    """Hold the sports lock for the duration of the block; released on success and on failure."""
    path = path or LOCK_PATH
    payload = acquire(purpose, path, log)
    try:
        yield payload
    finally:
        release(payload, path)
