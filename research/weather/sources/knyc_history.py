"""Historical timestamped KNYC observations from the IEM ASOS/METAR archive.

Source: Iowa Environmental Mesonet ASOS download service
(https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py), station NYC
(= KNYC, Central Park). Routine (report_type=3) and special (report_type=4)
METARs, timestamps in UTC. The archive supplies observation time only — no
publication / availability time — so availability is assumed downstream
(see research.weather.asof_observations.OBS_AVAILABILITY_LAG).

Raw CSV responses are cached per calendar month with a retrieval-metadata
sidecar. Observations are never derived from the CLI daily high.
"""

from __future__ import annotations

import csv
import hashlib
import io
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from kalshi import cache

IEM_ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
IEM_STATION = "NYC"
EXPECTED_STATION_ID = "KNYC"
SOURCE_ID = "iem_asos_archive"
TEMPERATURE_UNITS = "F"
KNYC_OBS_CACHE_DIR = cache.CACHE_ROOT / "weather" / "knyc_obs_iem"
REQUEST_TIMEOUT_S = 90
REQUEST_PAUSE_S = 5.0
RATE_LIMIT_BACKOFF_S = (20, 45, 90, 180)


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Contiguous [first_of_month, first_of_next_month) chunks covering [start, end]."""
    chunks: list[tuple[date, date]] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        nxt = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


def chunk_paths(chunk_start: date) -> tuple[Path, Path]:
    stem = f"{IEM_STATION}_{chunk_start.strftime('%Y%m')}"
    return KNYC_OBS_CACHE_DIR / f"{stem}.csv", KNYC_OBS_CACHE_DIR / f"{stem}.meta.json"


def _request_params(chunk_start: date, chunk_end: date) -> dict[str, Any]:
    # IEM treats the end date as exclusive.
    return {
        "station": IEM_STATION,
        "data": ["tmpf", "metar"],
        "tz": "Etc/UTC",
        "format": "onlycomma",
        "latlon": "no",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": ["3", "4"],
        "year1": chunk_start.year,
        "month1": chunk_start.month,
        "day1": chunk_start.day,
        "year2": chunk_end.year,
        "month2": chunk_end.month,
        "day2": chunk_end.day,
    }


def fetch_month(
    chunk_start: date,
    chunk_end: date,
    *,
    refresh: bool = False,
    now: datetime | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Return (raw_csv_text, retrieval_meta). Uses cache unless refresh.

    Chunks whose end lies in the future are fetched but never cached.
    """
    csv_path, meta_path = chunk_paths(chunk_start)
    if not refresh and csv_path.exists() and meta_path.exists():
        meta = cache.read_json(meta_path, default={}) or {}
        meta = dict(meta)
        meta["from_cache"] = True
        return csv_path.read_text(encoding="utf-8"), meta

    params = _request_params(chunk_start, chunk_end)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "source": SOURCE_ID,
        "url": IEM_ASOS_URL,
        "params": params,
        "chunk_start": chunk_start.isoformat(),
        "chunk_end_exclusive": chunk_end.isoformat(),
        "retrieved_at": retrieved_at,
        "from_cache": False,
    }
    response = None
    attempts: list[dict[str, Any]] = []
    for attempt, backoff_s in enumerate((0, *RATE_LIMIT_BACKOFF_S)):
        if backoff_s:
            time.sleep(backoff_s)
        try:
            response = requests.get(IEM_ASOS_URL, params=params, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as error:
            attempts.append({"attempt": attempt, "error": f"{type(error).__name__}: {error}"})
            response = None
            continue
        rate_limited = response.status_code in (429, 503) or (
            "too many requests" in response.text[:300].lower()
        )
        attempts.append({"attempt": attempt, "http_status": response.status_code, "rate_limited": rate_limited})
        if not rate_limited:
            break
    meta["attempts"] = attempts
    if response is None:
        meta["status"] = "error"
        meta["error"] = attempts[-1].get("error") if attempts else "no response"
        return None, meta

    meta["http_status"] = response.status_code
    if "too many requests" in response.text[:300].lower():
        meta["status"] = "error"
        meta["error"] = response.text[:300].strip()
        return None, meta
    meta["request_url"] = response.url
    if not response.ok:
        meta["status"] = "error"
        meta["error"] = response.text[:500]
        return None, meta

    text = response.text
    meta["status"] = "ok"
    meta["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    meta["bytes"] = len(text.encode("utf-8"))
    meta["row_count"] = max(0, len([l for l in text.splitlines() if l and not l.startswith("#")]) - 1)

    now = now or datetime.now(timezone.utc)
    end_utc = datetime(chunk_end.year, chunk_end.month, chunk_end.day, tzinfo=timezone.utc)
    if end_utc <= now:
        KNYC_OBS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.atomic_write_text(csv_path, text)
        cache.write_json(meta_path, meta)
        meta["cache_file"] = str(csv_path)
    else:
        meta["cache_file"] = None
        meta["note"] = "chunk extends into the future; not cached"
    return text, meta


def _normalize_station(raw: str) -> str:
    raw = (raw or "").strip().upper()
    if len(raw) == 3 and raw.isalpha():
        return f"K{raw}"
    return raw


def parse_iem_asos_csv(
    text: str,
    retrieval_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse IEM ``format=onlycomma`` CSV into normalized observation records.

    No validation here beyond parsing — validation lives in asof_observations.
    Records keep raw fields so rejected rows remain auditable.
    """
    meta = retrieval_meta or {}
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    records: list[dict[str, Any]] = []
    for row in reader:
        station_raw = str(row.get("station") or "").strip()
        valid_raw = str(row.get("valid") or "").strip()
        tmpf_raw = str(row.get("tmpf") if row.get("tmpf") is not None else "").strip()
        metar = str(row.get("metar") or "").strip()

        obs_time: str | None = None
        try:
            obs_time = (
                datetime.strptime(valid_raw, "%Y-%m-%d %H:%M")
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
        except ValueError:
            obs_time = None

        temperature_f: float | None
        try:
            temperature_f = None if tmpf_raw in ("", "M") else float(tmpf_raw)
        except ValueError:
            temperature_f = None

        report_kind = None
        if metar.startswith("SPECI"):
            report_kind = "SPECI"
        elif metar:
            report_kind = "METAR"

        records.append(
            {
                "station_raw": station_raw,
                "station_id": _normalize_station(station_raw),
                "observation_time_raw": valid_raw,
                "observation_time_utc": obs_time,
                "availability_time_utc": None,
                "temperature_raw": tmpf_raw,
                "temperature_f": temperature_f,
                "units": TEMPERATURE_UNITS,
                "report_kind": report_kind,
                "raw_metar": metar,
                "source": SOURCE_ID,
                "retrieved_at": meta.get("retrieved_at"),
                "source_url": meta.get("request_url") or meta.get("url"),
                "cache_file": meta.get("cache_file"),
                "response_sha256": meta.get("sha256"),
            }
        )
    return records


def load_knyc_observations(
    start: date,
    end: date,
    *,
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch (or reuse cache) and parse all monthly chunks covering [start, end]."""
    log = progress or (lambda _m: None)
    records: list[dict[str, Any]] = []
    fetch_log: list[dict[str, Any]] = []
    for chunk_start, chunk_end in month_chunks(start, end):
        text, meta = fetch_month(chunk_start, chunk_end, refresh=refresh)
        if not meta.get("from_cache"):
            time.sleep(REQUEST_PAUSE_S)
        csv_path, _ = chunk_paths(chunk_start)
        if meta.get("from_cache"):
            meta["cache_file"] = str(csv_path)
        fetch_log.append({k: v for k, v in meta.items() if k != "params"})
        if text is None:
            log(f"KNYC obs chunk {chunk_start:%Y-%m} failed: {meta.get('error')}")
            continue
        parsed = parse_iem_asos_csv(text, meta)
        log(f"KNYC obs chunk {chunk_start:%Y-%m}: {len(parsed)} records")
        records.extend(parsed)
    return records, fetch_log
