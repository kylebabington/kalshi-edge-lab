"""Shared plumbing for the sports pipeline: paths, hashing, byte-exact raw cache, write-once files.

Every network read goes through :class:`Fetcher`. Responses are stored byte-for-byte with a
metadata sidecar (URL, params, status, retrieval time, sha256). With ``cache_only=True`` a missing
cache entry raises instead of touching the network, so reruns reproduce from disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlencode

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO_ROOT / "data" / "cache" / "sports" / "raw"
SPORTS_DATA = REPO_ROOT / "data" / "sports"
NORMALIZED = SPORTS_DATA / "normalized"
CROSSWALK = SPORTS_DATA / "crosswalk"
PREDICTIONS = SPORTS_DATA / "predictions"
RESULTS = REPO_ROOT / "data" / "results" / "sports_phase1"
PROTOCOLS = Path(__file__).resolve().parent / "protocols"
DISCOVERY_RUN = REPO_ROOT / "data" / "cache" / "sports" / "kalshi_discovery" / "20261008T2225Z"

USER_AGENT = "kalshi-edge-lab sports research (non-commercial; low rate)"


class CacheMiss(RuntimeError):
    """Raised in cache-only mode when a request has never been fetched."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    if len(v) == 16 + 6 and v[10] == "T" and v.count(":") == 2:  # 2025-01-02T00:00+00:00
        v = v[:16] + ":00" + v[16:]
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict]) -> str:
    """Write rows deterministically; return the sha256 of the bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(canonical_json(r) + b"\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return sha256_bytes(data)


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return sha256_bytes(data)


class WriteOnceError(RuntimeError):
    pass


def write_once_jsonl(path: Path, rows: list[dict]) -> str:
    """Write a journal once. Re-writing identical bytes is allowed; different bytes are refused."""
    data = b"".join(canonical_json(r) + b"\n" for r in rows)
    digest = sha256_bytes(data)
    if path.exists():
        existing = sha256_file(path)
        if existing != digest:
            raise WriteOnceError(f"{path} exists with different content ({existing[:12]} != {digest[:12]}); "
                                 "bump the protocol version instead of overwriting predictions")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return digest


@dataclass
class Raw:
    url: str
    params: dict
    status: int
    body: bytes
    meta: dict

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def request_key(url: str, params: dict | None) -> str:
    q = urlencode(sorted((k, str(v)) for k, v in (params or {}).items()))
    return sha256_bytes(f"{url}?{q}".encode("utf-8"))


class Fetcher:
    """Single-flight, rate-limited HTTP GET with a byte-exact on-disk cache.

    One request at a time per process (a lock), plus a minimum interval per host, keeps sports
    downloads from crowding the weather collector's network use.
    """

    def __init__(self, cache_only: bool = False, min_interval: float = 0.4, root: Path = RAW_ROOT,
                 max_retries: int = 6, timeout: float = 45.0, session: requests.Session | None = None):
        self.cache_only = cache_only
        self.min_interval = min_interval
        self.root = root
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self.stats = {"cache_hits": 0, "network": 0, "retries": 0, "errors": 0}

    def _paths(self, source: str, key: str) -> tuple[Path, Path]:
        d = self.root / source / key[:2]
        return d / f"{key}.body", d / f"{key}.meta.json"

    def cached(self, source: str, url: str, params: dict | None = None) -> Raw | None:
        key = request_key(url, params)
        body_p, meta_p = self._paths(source, key)
        if body_p.exists() and meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            body = body_p.read_bytes()
            if sha256_bytes(body) != meta.get("sha256"):
                raise RuntimeError(f"raw cache corrupted: {body_p}")
            return Raw(url, params or {}, meta["status"], body, meta)
        return None

    def get(self, source: str, url: str, params: dict | None = None, *,
            cache_statuses: tuple[int, ...] = (200, 400, 404), headers: dict | None = None) -> Raw:
        hit = self.cached(source, url, params)
        if hit is not None:
            self.stats["cache_hits"] += 1
            return hit
        if self.cache_only:
            raise CacheMiss(f"{source}: {url} {params}")
        host = url.split("/")[2]
        attempt = 0
        with self._lock:
            while True:
                wait = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
                if wait > 0:
                    time.sleep(wait)
                started = time.monotonic()
                retrieved = utc_now_iso()
                try:
                    resp = self.session.get(url, params=params, timeout=self.timeout,
                                            headers={"User-Agent": USER_AGENT, **(headers or {})})
                    status, body, ctype = resp.status_code, resp.content, resp.headers.get("content-type")
                except requests.RequestException as exc:
                    status, body, ctype = -1, str(exc).encode("utf-8"), None
                self._last[host] = time.monotonic()
                self.stats["network"] += 1
                if status in (429, 500, 502, 503, 504, -1) and attempt < self.max_retries:
                    attempt += 1
                    self.stats["retries"] += 1
                    time.sleep(min(60.0, 2.0 ** attempt))
                    continue
                break
        meta = {"url": url, "params": params or {}, "status": status, "retrieved_at": retrieved,
                "elapsed_s": round(time.monotonic() - started, 3), "content_type": ctype,
                "bytes": len(body), "sha256": sha256_bytes(body), "attempts": attempt + 1}
        raw = Raw(url, params or {}, status, body, meta)
        if status in cache_statuses:
            key = request_key(url, params)
            body_p, meta_p = self._paths(source, key)
            body_p.parent.mkdir(parents=True, exist_ok=True)
            body_p.write_bytes(body)
            meta_p.write_text(json.dumps(meta, sort_keys=True, indent=1), encoding="utf-8")
        else:
            self.stats["errors"] += 1
        return raw
