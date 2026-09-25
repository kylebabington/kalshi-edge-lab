"""Disk cache helpers for Kalshi market-efficiency research."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "data" / "cache"
RESULTS_ROOT = REPO_ROOT / "data" / "results"


def ensure_dirs() -> None:
    for path in (
        CACHE_ROOT,
        CACHE_ROOT / "inventory",
        CACHE_ROOT / "events",
        CACHE_ROOT / "series",
        CACHE_ROOT / "candles",
        CACHE_ROOT / "fees",
        CACHE_ROOT / "logs",
        CACHE_ROOT / "weather",
        CACHE_ROOT / "weather" / "gfs",
        CACHE_ROOT / "weather" / "gefs",
        CACHE_ROOT / "weather" / "observations",
        CACHE_ROOT / "weather" / "resolution",
        CACHE_ROOT / "weather" / "calibration",
        CACHE_ROOT / "weather" / "clinyc",
        RESULTS_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically so failed runs never wipe prior cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can briefly lock the destination (AV/indexer); retry replace.
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                os.replace(tmp_name, path)
                last_error = None
                break
            except PermissionError as error:
                last_error = error
                time.sleep(0.05 * (2**attempt))
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    else:
        text = json.dumps(payload, indent=2, sort_keys=True)
    atomic_write_text(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    # Windows can briefly lock files (AV/indexer); retry open+read.
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except PermissionError as error:
            last_error = error
            time.sleep(0.05 * (2**attempt))
        except OSError as error:
            # Treat sharing violations similarly on Windows.
            if getattr(error, "winerror", None) not in (5, 32) and error.errno not in (
                13,
            ):
                raise
            last_error = error
            time.sleep(0.05 * (2**attempt))
    if last_error is not None:
        raise last_error
    return default


def write_jsonl(path: Path, rows: list[dict]) -> None:
    lines = [json.dumps(row, sort_keys=True) for row in rows]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def inventory_path(name: str) -> Path:
    return CACHE_ROOT / "inventory" / f"{name}.jsonl"


def inventory_meta_path(name: str) -> Path:
    return CACHE_ROOT / "inventory" / f"{name}_meta.json"


def inventory_pages_dir(name: str) -> Path:
    return CACHE_ROOT / "inventory" / name / "pages"


def inventory_pages_meta_path(name: str) -> Path:
    return CACHE_ROOT / "inventory" / name / "pages_meta.json"


def usable_inventory_path() -> Path:
    return CACHE_ROOT / "inventory" / "usable_inventory.jsonl"


def usable_recent_path() -> Path:
    return CACHE_ROOT / "inventory" / "usable_recent.jsonl"


def inventory_snapshot_path() -> Path:
    return CACHE_ROOT / "inventory" / "inventory_snapshot.json"


def event_path(event_ticker: str) -> Path:
    safe = event_ticker.replace("/", "_")
    return CACHE_ROOT / "events" / f"{safe}.json"


def series_path(series_ticker: str) -> Path:
    safe = series_ticker.replace("/", "_")
    return CACHE_ROOT / "series" / f"{safe}.json"


def candle_path(ticker: str, period_interval: int = 60) -> Path:
    safe = ticker.replace("/", "_")
    return CACHE_ROOT / "candles" / f"{safe}_{period_interval}.json"


def fee_changes_path() -> Path:
    return CACHE_ROOT / "fees" / "fee_changes.json"


def fee_series_path(series_ticker: str) -> Path:
    """Per-series fee resolution cache (metadata + fee_changes)."""
    safe = series_ticker.replace("/", "_")
    return CACHE_ROOT / "fees" / f"{safe}.json"


def cutoff_path() -> Path:
    return CACHE_ROOT / "inventory" / "cutoff.json"


def failed_tickers_path() -> Path:
    return CACHE_ROOT / "logs" / "failed_tickers.jsonl"


def log_failed_ticker(ticker: str, reason: str, context: dict | None = None) -> None:
    ensure_dirs()
    row = {"ticker": ticker, "reason": reason}
    if context:
        row.update(context)
    append_jsonl(failed_tickers_path(), row)
