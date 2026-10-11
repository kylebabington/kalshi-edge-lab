"""HTTP session handed to the frozen Fetcher: request accounting plus shared-host coordination.

The frozen Fetcher already serializes requests (one at a time), spaces requests to one host by the
frozen 0.5 s and bounds retries. This session adds, for hosts the weather collector also uses
(Kalshi), a wider spacing while the weather cycle holds its lock. It never changes a request, its
parameters or its response bytes.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from kalshi.client import BASE_URL as WEATHER_KALSHI_BASE

from ..adapters import ESPN_BASE, KALSHI_BASE
from .lock import WEATHER_LOCK_PATH, lock_state

SPORTS_HOSTS = {urlsplit(KALSHI_BASE).hostname, urlsplit(ESPN_BASE).hostname}
WEATHER_HOSTS = {urlsplit(WEATHER_KALSHI_BASE).hostname}
SHARED_HOSTS = SPORTS_HOSTS & WEATHER_HOSTS
SHARED_MIN_INTERVAL_S = 1.0
WEATHER_LOCK_RECHECK_S = 30.0


class CoordinatedSession:
    def __init__(self, base=None, weather_lock=WEATHER_LOCK_PATH, shared_min_interval: float = SHARED_MIN_INTERVAL_S,
                 clock=time.monotonic, sleep=time.sleep):
        self.base = base or requests.Session()
        self.weather_lock = weather_lock
        self.shared_min_interval = shared_min_interval
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._weather = (float("-inf"), False)
        self.stats: dict[str, dict] = {}

    def weather_active(self) -> bool:
        t = self.clock()
        if t - self._weather[0] >= WEATHER_LOCK_RECHECK_S:
            self._weather = (t, bool(lock_state(self.weather_lock)["held"]))
        return self._weather[1]

    def get(self, url, **kwargs):
        host = urlsplit(url).hostname or ""
        st = self.stats.setdefault(host, {"requests": 0, "seconds": 0.0, "errors": 0, "coordinated_waits": 0})
        if host in SHARED_HOSTS and self.weather_active():
            wait = self.shared_min_interval - (self.clock() - self._last.get(host, float("-inf")))
            if wait > 0:
                st["coordinated_waits"] += 1
                self.sleep(wait)
        t0 = self.clock()
        try:
            resp = self.base.get(url, **kwargs)
        except Exception:
            st["errors"] += 1
            raise
        finally:
            self._last[host] = self.clock()
            st["requests"] += 1
            st["seconds"] = round(st["seconds"] + self._last[host] - t0, 3)
        if resp.status_code >= 400:
            st["errors"] += 1
        return resp
