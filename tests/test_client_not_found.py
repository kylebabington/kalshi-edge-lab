"""Client/meta-cache behavior for permanent API misses."""

from __future__ import annotations

from pathlib import Path

import pytest

from kalshi import cache
from kalshi.client import KalshiClient, KalshiNotFoundError
from kalshi.historical import load_or_fetch_json


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, status_code: int = 404):
        self.status_code = status_code
        self.calls = 0

    def request(self, method, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResponse(self.status_code, text="missing")


def test_404_does_not_retry():
    session = _FakeSession(404)
    client = KalshiClient(session=session, min_delay_sec=0, progress=lambda _m: None)
    with pytest.raises(KalshiNotFoundError):
        client.get("/series/MISSING")
    assert session.calls == 1


def test_missing_series_is_cached(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    path = cache.series_path("GONE")
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        raise KalshiNotFoundError("Not found: /series/GONE")

    assert load_or_fetch_json(path, fetch, refresh=False) is None
    assert calls["n"] == 1
    assert path.exists()
    assert cache.read_json(path)["_missing"] is True

    # Second call should use negative cache and not refetch.
    assert load_or_fetch_json(path, fetch, refresh=False) is None
    assert calls["n"] == 1
