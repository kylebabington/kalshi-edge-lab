"""Settled-market inventory: historical + recent, enrichment, audit."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from kalshi import cache
from kalshi.client import KalshiClient, KalshiAPIError, KalshiNotFoundError


ProgressFn = Callable[[str], None]


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "unknown"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"

INVENTORY_SCHEMA_VERSION = 2
USABLE_INVENTORY_FIELDS = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "category",
    "title",
    "subtitle",
    "market_type",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "expiration_time",
    "latest_expiration_time",
    "occurrence_datetime",
    "settlement_ts",
    "can_close_early",
    "result",
    "volume",
    "open_interest",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "settlement_value_dollars",
    "data_source",
)
REQUIRED_RESEARCH_FIELDS = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "category",
    "close_time",
    "result",
    "data_source",
)
VALID_DATA_SOURCES = ("historical", "recent")
_COMBO_MARKET_TYPES = frozenset({"multivariate", "combo"})
_REQUIRED_SAMPLE_MODULUS = 10_000


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_binary_settlement(market: dict) -> bool:
    """True only for normal YES/NO settlements."""
    result = market.get("result")
    if result not in ("yes", "no"):
        return False
    market_type = str(market.get("market_type") or "binary").lower()
    if market_type and market_type not in ("binary",):
        return False
    settlement_value = market.get("settlement_value_dollars")
    if settlement_value is not None:
        try:
            sv = float(settlement_value)
            if result == "yes" and sv not in (1.0, 1):
                # Allow near-1 / near-0 floating representations.
                if abs(sv - 1.0) > 1e-6 and abs(sv - 0.0) > 1e-6:
                    # YES should settle ~1; some APIs only fill YES side value.
                    if abs(sv - 1.0) > 0.01:
                        return False
            if result == "no" and abs(sv - 0.0) > 0.01 and abs(sv - 1.0) > 1e-6:
                # NO win often has YES settlement_value = 0.
                if abs(sv - 0.0) > 0.01:
                    return False
        except (TypeError, ValueError):
            return False
    return True


def settlement_skip_reason(market: dict) -> str | None:
    result = market.get("result")
    if result in (None, ""):
        return "missing_result"
    if result not in ("yes", "no"):
        return "nonstandard_settlement"
    if not is_binary_settlement(market):
        return "nonstandard_settlement"
    return None


def inventory_skip_reason(market: dict) -> str | None:
    """Single source of truth for inventory eligibility (build, enrich, reconcile)."""
    mve = market.get("mve_selected_legs") or market.get("market_type")
    if market.get("is_mve") or str(mve).lower() in _COMBO_MARKET_TYPES:
        return "excluded_combo"
    if not market.get("close_time"):
        return "missing_close_time"
    return settlement_skip_reason(market)


def record_inventory_skip(audit: "InventoryAudit", reason: str) -> None:
    audit.skip_reasons[reason] += 1
    if reason == "excluded_combo":
        audit.excluded_combos += 1
    elif reason == "missing_close_time":
        audit.missing_close_time += 1
    elif reason == "missing_result":
        audit.missing_result += 1
    elif reason == "nonstandard_settlement":
        audit.nonstandard_settlement += 1


def unique_excluded_count(audit: "InventoryAudit") -> int:
    if audit.skip_reasons:
        return int(sum(audit.skip_reasons.values()))
    return int(
        audit.excluded_combos
        + audit.missing_result
        + audit.missing_close_time
        + audit.nonstandard_settlement
    )


def counts_reconcile(audit: "InventoryAudit") -> bool:
    return audit.combined_unique == audit.usable + unique_excluded_count(audit)


@dataclass
class InventoryAudit:
    historical_count: int = 0
    recent_count: int = 0
    combined_unique: int = 0
    excluded_combos: int = 0
    missing_result: int = 0
    missing_close_time: int = 0
    nonstandard_settlement: int = 0
    unique_excluded: int = 0
    usable: int = 0
    categories: Counter = field(default_factory=Counter)
    skip_reasons: Counter = field(default_factory=Counter)
    cutoff: dict = field(default_factory=dict)
    inventory_integrity_valid: bool | None = None


@dataclass
class InventoryStreamStatus:
    name: str
    pages: int = 0
    count: int = 0
    complete: bool = False
    truncated_by_limit: bool = False


def lean_usable_row(row: dict) -> dict:
    """Serializable usable-inventory row without nested payloads."""
    return {key: row.get(key) for key in USABLE_INVENTORY_FIELDS}


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def field_missing(row: dict, key: str) -> bool:
    if key == "data_source":
        return row.get("data_source") not in VALID_DATA_SOURCES
    return _is_blank(row.get(key))


def required_research_fields_ok(row: dict) -> bool:
    return all(not field_missing(row, key) for key in REQUIRED_RESEARCH_FIELDS)


def missing_field_counts(row: dict) -> tuple[dict[str, int], dict[str, int]]:
    required: dict[str, int] = {}
    optional: dict[str, int] = {}
    for key in USABLE_INVENTORY_FIELDS:
        if not field_missing(row, key):
            continue
        if key in REQUIRED_RESEARCH_FIELDS:
            required[key] = 1
        else:
            optional[key] = 1
    return required, optional


def compute_inventory_hash(rows: list[dict]) -> str:
    """Deterministic SHA-256 over stable inventory identity fields.

    Uses sorted per-row digests so large inventories do not require one giant
    concatenated payload in memory.
    """
    digests: list[bytes] = []
    for row in rows:
        record = {
            "ticker": row.get("ticker") or "",
            "event_ticker": row.get("event_ticker") or "",
            "series_ticker": row.get("series_ticker") or "",
            "close_time": row.get("close_time") or "",
            "result": row.get("result") or "",
        }
        payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
        digests.append(hashlib.sha256(payload.encode("utf-8")).digest())
    digests.sort()
    h = hashlib.sha256()
    for digest in digests:
        h.update(digest)
    return h.hexdigest()


def compute_inventory_hash_from_digests(digests: list[bytes]) -> str:
    ordered = sorted(digests)
    h = hashlib.sha256()
    for digest in ordered:
        h.update(digest)
    return h.hexdigest()


def identity_digest(row: dict) -> bytes:
    record = {
        "ticker": row.get("ticker") or "",
        "event_ticker": row.get("event_ticker") or "",
        "series_ticker": row.get("series_ticker") or "",
        "close_time": row.get("close_time") or "",
        "result": row.get("result") or "",
    }
    payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).digest()


def write_usable_inventory(rows: list[dict]) -> None:
    """Atomically persist lean usable inventory."""
    cache.ensure_dirs()
    lean = [lean_usable_row(row) for row in rows]
    cache.write_jsonl(cache.usable_inventory_path(), lean)


def write_usable_inventory_from_path(src: Path) -> None:
    """Atomically publish a pre-written usable inventory temp file."""
    cache.ensure_dirs()
    dest = cache.usable_inventory_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)


def stream_merge_usable_inventory(
    *,
    existing_path: Path,
    recent_path: Path,
    dest_path: Path | None = None,
) -> dict[str, int]:
    """
    Memory-safe merge: keep existing rows whose ticker is not in recent,
    then append all recent usable rows. Never loads the 11M file as a list.

    Existing combined rows keep their ``data_source``. Recent catalog rows are
    normalized with ``data_source="recent"``.
    """
    cache.ensure_dirs()
    dest = dest_path or cache.usable_inventory_path()
    recent_tickers: set[str] = set()
    recent_count = 0
    with recent_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = row.get("ticker")
            if ticker:
                recent_tickers.add(str(ticker))
            recent_count += 1

    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent),
        prefix=".usable_inventory.merge.",
        suffix=".jsonl.tmp",
    )
    kept = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            if existing_path.exists():
                with existing_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        raw = line.strip()
                        if not raw:
                            continue
                        row = json.loads(raw)
                        ticker = row.get("ticker")
                        if ticker and str(ticker) in recent_tickers:
                            continue
                        lean = normalize_inventory_market(row)
                        out.write(json.dumps(lean, sort_keys=True) + "\n")
                        kept += 1
            with recent_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    row = json.loads(raw)
                    lean = normalize_inventory_market(row, data_source="recent")
                    out.write(json.dumps(lean, sort_keys=True) + "\n")
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return {
        "kept_existing": kept,
        "recent_rows": recent_count,
        "recent_tickers": len(recent_tickers),
        "combined_estimate": kept + recent_count,
    }


def stream_complete_inventory(
    historical: InventoryStreamStatus,
    recent: InventoryStreamStatus,
) -> bool:
    """True only when both streams naturally finished and neither hit the limit."""
    return (
        historical.complete
        and recent.complete
        and not historical.truncated_by_limit
        and not recent.truncated_by_limit
    )


def _parse_date_range(date_range: str) -> tuple[str | None, str | None]:
    text = str(date_range or "n/a")
    if "->" not in text:
        return None, None
    left, right = text.split("->", 1)
    return (left.strip() or None, right.strip() or None)


def count_missing_fields(rows: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    required: Counter[str] = Counter()
    optional: Counter[str] = Counter()
    for row in rows:
        req, opt = missing_field_counts(row)
        required.update(req)
        optional.update(opt)
    return dict(required), dict(optional)


def evaluate_inventory_integrity(
    *,
    usable_count: int,
    date_min: str | None,
    date_max: str | None,
    years: list[int],
    unique_events: int,
    unique_series: int,
    count_reconciliation_valid: bool,
    required_field_missing_counts: dict[str, int] | None = None,
    required_sample_valid: bool = True,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if usable_count <= 0:
        errors.append("usable_count is 0")
    if not date_min:
        errors.append("date_min is null")
    if not date_max:
        errors.append("date_max is null")
    if not years:
        errors.append("years is empty")
    if unique_events <= 0:
        errors.append("unique_events is 0")
    if unique_series <= 0:
        errors.append("unique_series is 0")
    if not count_reconciliation_valid:
        errors.append("count reconciliation failed")
    for key in REQUIRED_RESEARCH_FIELDS:
        n = int((required_field_missing_counts or {}).get(key) or 0)
        if n:
            errors.append(f"required field {key} missing on {n:,} rows")
    if not required_sample_valid:
        errors.append("required-field sample of historical/recent rows failed")
    return (not errors), errors


def build_inventory_snapshot(
    *,
    audit: InventoryAudit,
    usable: list[dict],
    historical: InventoryStreamStatus,
    recent: InventoryStreamStatus,
    inventory_max_items: int | None,
    api_base_url: str,
    diagnostics: dict[str, Any],
    inventory_hash: str | None = None,
) -> dict[str, Any]:
    date_min = diagnostics.get("date_min")
    date_max = diagnostics.get("date_max")
    if not date_min or not date_max:
        parsed_min, parsed_max = _parse_date_range(
            str(diagnostics.get("inventory_date_range") or "n/a")
        )
        date_min = date_min or parsed_min
        date_max = date_max or parsed_max

    required_missing = dict(diagnostics.get("required_field_missing_counts") or {})
    optional_missing = dict(diagnostics.get("optional_field_missing_counts") or {})
    if not required_missing and not optional_missing and usable:
        required_missing, optional_missing = count_missing_fields(usable)

    recon_valid = diagnostics.get("count_reconciliation_valid")
    if recon_valid is None:
        recon_valid = counts_reconcile(audit)
    recon_valid = bool(recon_valid)

    sample_valid = diagnostics.get("required_sample_valid")
    if sample_valid is None:
        sample_valid = True
        if usable:
            sample_valid = all(required_research_fields_ok(row) for row in usable)

    years = list(diagnostics.get("inventory_years") or [])
    unique_events = int(diagnostics.get("inventory_unique_events") or 0)
    unique_series = int(diagnostics.get("inventory_unique_series") or 0)
    integrity_valid, integrity_errors = evaluate_inventory_integrity(
        usable_count=int(audit.usable),
        date_min=date_min,
        date_max=date_max,
        years=years,
        unique_events=unique_events,
        unique_series=unique_series,
        count_reconciliation_valid=recon_valid,
        required_field_missing_counts=required_missing,
        required_sample_valid=bool(sample_valid),
    )
    audit.unique_excluded = unique_excluded_count(audit)
    audit.inventory_integrity_valid = integrity_valid

    snapshot = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "historical_cutoff": audit.cutoff,
        "historical_pages_downloaded": historical.pages,
        "recent_pages_downloaded": recent.pages,
        "raw_historical_count": audit.historical_count,
        "raw_recent_count": audit.recent_count,
        "combined_unique_count": audit.combined_unique,
        "unique_excluded_count": audit.unique_excluded,
        "unique_excluded_by_reason": dict(audit.skip_reasons),
        "usable_count": audit.usable,
        "date_min": date_min,
        "date_max": date_max,
        "years": years,
        "categories": dict(diagnostics.get("inventory_categories") or {}),
        "unique_events": unique_events,
        "unique_series": unique_series,
        "inventory_max_items": inventory_max_items,
        "complete_inventory": stream_complete_inventory(historical, recent),
        "historical_complete": historical.complete,
        "recent_complete": recent.complete,
        "historical_truncated_by_limit": historical.truncated_by_limit,
        "recent_truncated_by_limit": recent.truncated_by_limit,
        "inventory_hash": inventory_hash or compute_inventory_hash(usable),
        "api_base_url": api_base_url,
        "required_field_missing_counts": required_missing,
        "optional_field_missing_counts": optional_missing,
        "missing_open_time": int(optional_missing.get("open_time") or 0),
        "count_reconciliation_valid": recon_valid,
        "inventory_integrity_valid": integrity_valid,
        "inventory_integrity_errors": integrity_errors,
        "required_sample_valid": bool(sample_valid),
    }
    cache.ensure_dirs()
    cache.write_json(cache.inventory_snapshot_path(), snapshot)
    return snapshot


def iter_inventory_page_markets(name: str, *, max_items: int | None = None) -> Iterator[dict]:
    """Yield cached page markets without accumulating the full inventory."""
    pages_dir = cache.inventory_pages_dir(name)
    if not pages_dir.exists():
        return
    emitted = 0
    for path in sorted(pages_dir.glob("page_*.json")):
        payload = cache.read_json(path, default={}) or {}
        for market in payload.get("markets") or []:
            yield market
            emitted += 1
            if max_items is not None and emitted >= max_items:
                return


def _remove_sqlite_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix) if suffix else path
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def classify_cached_inventory_pages(
    *,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Exact unique-ticker classification from cached raw pages (disk-backed SQLite)."""
    log = progress or (lambda _m: None)
    cache.ensure_dirs()
    tmp_dir = cache.CACHE_ROOT / "inventory" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(tmp_dir),
        prefix=".inventory_reconcile.",
        suffix=".sqlite",
    )
    os.close(fd)
    db_path = Path(tmp_name)
    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA temp_store=FILE")
        con.execute(
            "CREATE TABLE markets (ticker TEXT PRIMARY KEY, skip TEXT, source TEXT)"
        )
        con.execute("BEGIN")

        def _upsert(name: str, *, replace: bool) -> int:
            sql = (
                "INSERT OR REPLACE INTO markets(ticker, skip, source) VALUES (?, ?, ?)"
                if replace
                else "INSERT OR IGNORE INTO markets(ticker, skip, source) VALUES (?, ?, ?)"
            )
            batch: list[tuple[str, str | None, str]] = []
            scanned = 0
            for market in iter_inventory_page_markets(name):
                ticker = market.get("ticker")
                if not ticker:
                    continue
                skip = inventory_skip_reason(market)
                batch.append((str(ticker), skip, name))
                scanned += 1
                if len(batch) >= 5_000:
                    con.executemany(sql, batch)
                    batch.clear()
                if scanned % 100_000 == 0:
                    log(f"Classifying {name}: scanned={scanned:,}")
            if batch:
                con.executemany(sql, batch)
            log(f"Classified {name}: scanned={scanned:,}")
            return scanned

        raw_historical = _upsert("historical", replace=False)
        raw_recent = _upsert("recent_settled", replace=True)
        con.commit()

        combined_unique = int(
            con.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
        )
        unique_excluded = int(
            con.execute(
                "SELECT COUNT(*) FROM markets WHERE skip IS NOT NULL"
            ).fetchone()[0]
        )
        usable_unique = combined_unique - unique_excluded
        skip_rows = con.execute(
            "SELECT skip, COUNT(*) FROM markets WHERE skip IS NOT NULL GROUP BY skip"
        ).fetchall()
        skip_reasons = {str(reason): int(count) for reason, count in skip_rows}
        return {
            "raw_historical_scanned": raw_historical,
            "raw_recent_scanned": raw_recent,
            "combined_unique": combined_unique,
            "unique_excluded": unique_excluded,
            "usable_unique": usable_unique,
            "skip_reasons": skip_reasons,
        }
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
        _remove_sqlite_files(db_path)


def stream_combined_inventory_diagnostics(
    path: Path,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Stream canonical combined inventory for dates, uniques, and missing fields."""
    log = progress or (lambda _m: None)
    by_year: Counter[int] = Counter()
    by_category: Counter[str] = Counter()
    by_event: Counter[str] = Counter()
    by_series: Counter[str] = Counter()
    by_day: Counter[str] = Counter()
    required_missing: Counter[str] = Counter()
    optional_missing: Counter[str] = Counter()
    series_set: set[str] = set()
    event_set: set[str] = set()
    digests: list[bytes] = []
    date_min = None
    date_max = None
    usable_count = 0
    first_historical: dict | None = None
    last_historical: dict | None = None
    first_recent: dict | None = None
    last_recent: dict | None = None
    hashed_samples: list[dict] = []
    started_at = time.monotonic()

    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                row = json.loads(raw)
                usable_count += 1
                req, opt = missing_field_counts(row)
                required_missing.update(req)
                optional_missing.update(opt)
                digests.append(identity_digest(row))

                series_ticker = row.get("series_ticker")
                event_ticker = row.get("event_ticker")
                if series_ticker:
                    series_set.add(str(series_ticker))
                    by_series[str(series_ticker)] += 1
                if event_ticker:
                    event_set.add(str(event_ticker))
                    by_event[str(event_ticker)] += 1
                category = row.get("category") or "Unknown"
                by_category[str(category)] += 1

                ts = _parse_ts(row.get("close_time"))
                if ts is not None:
                    by_year[ts.year] += 1
                    by_day[ts.date().isoformat()] += 1
                    if date_min is None or ts < date_min:
                        date_min = ts
                    if date_max is None or ts > date_max:
                        date_max = ts

                source = row.get("data_source")
                if source == "historical":
                    if first_historical is None:
                        first_historical = row
                    last_historical = row
                elif source == "recent":
                    if first_recent is None:
                        first_recent = row
                    last_recent = row

                ticker = str(row.get("ticker") or "")
                if ticker:
                    digest_int = int(
                        hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8],
                        16,
                    )
                    if digest_int % _REQUIRED_SAMPLE_MODULUS == 0:
                        hashed_samples.append(row)

                if usable_count % 250_000 == 0:
                    elapsed = max(0.001, time.monotonic() - started_at)
                    log(
                        f"Streaming combined inventory: {usable_count:,} rows "
                        f"({usable_count / elapsed:,.0f}/s)"
                    )

    sample_rows: list[dict] = []
    for candidate in (
        first_historical,
        last_historical,
        first_recent,
        last_recent,
        *hashed_samples,
    ):
        if candidate is not None:
            sample_rows.append(candidate)

    sources_seen = {
        row.get("data_source")
        for row in (first_historical, last_historical, first_recent, last_recent)
        if row is not None
    }
    required_sample_valid = bool(sample_rows) and all(
        required_research_fields_ok(row) for row in sample_rows
    )
    if "historical" in sources_seen and first_historical is None:
        required_sample_valid = False
    if "recent" in sources_seen and first_recent is None:
        required_sample_valid = False
    if usable_count > 0 and not sources_seen:
        required_sample_valid = False

    def _top(counter: Counter[str]) -> tuple[str, int]:
        if not counter:
            return ("n/a", 0)
        key, count = counter.most_common(1)[0]
        return (key, count)

    return {
        "usable_count": usable_count,
        "inventory_markets": usable_count,
        "inventory_date_range": (
            f"{date_min.date().isoformat()} -> {date_max.date().isoformat()}"
            if date_min and date_max
            else "n/a"
        ),
        "date_min": date_min.date().isoformat() if date_min else None,
        "date_max": date_max.date().isoformat() if date_max else None,
        "inventory_years": sorted(by_year),
        "inventory_categories": dict(by_category.most_common()),
        "inventory_unique_series": len(series_set),
        "inventory_unique_events": len(event_set),
        "markets_by_year": dict(sorted(by_year.items())),
        "largest_event": _top(by_event),
        "largest_series": _top(by_series),
        "largest_day": _top(by_day),
        "required_field_missing_counts": dict(required_missing),
        "optional_field_missing_counts": dict(optional_missing),
        "missing_open_time": int(optional_missing.get("open_time") or 0),
        "required_sample_valid": required_sample_valid,
        "required_sample_size": len(sample_rows),
        "data_sources_seen": sorted(str(s) for s in sources_seen if s),
        "inventory_hash": compute_inventory_hash_from_digests(digests),
    }


def dedupe_markets_by_ticker(markets: list[dict]) -> list[dict]:
    by_ticker: dict[str, dict] = {}
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue
        # Prefer the first occurrence; historical usually listed first by caller.
        if ticker not in by_ticker:
            by_ticker[ticker] = market
    return list(by_ticker.values())


def series_ticker_from_market(market: dict) -> str | None:
    if market.get("series_ticker"):
        return market["series_ticker"]
    event_ticker = str(market.get("event_ticker") or "").strip()
    if not event_ticker:
        return None
    # Common pattern: SERIES-DATE or SERIES-EVENTSUFFIX. If there is no hyphen,
    # the event ticker itself is the series identity.
    if "-" in event_ticker:
        return event_ticker.split("-", 1)[0]
    return event_ticker


def normalize_inventory_market(
    market: dict,
    *,
    data_source: str | None = None,
    category: str | None = None,
) -> dict:
    """Project a raw API market or existing lean row onto the canonical schema.

    ``data_source`` is only overridden when the caller passes it explicitly.
    Combined re-merge must omit it so existing ``row["data_source"]`` is kept.
    """
    source = data_source if data_source is not None else market.get("data_source")
    resolved_category = category if category is not None else market.get("category")
    row = {
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "series_ticker": series_ticker_from_market(market),
        "category": resolved_category or "Unknown",
        "title": _coalesce(market.get("title"), market.get("yes_sub_title")),
        "subtitle": _coalesce(market.get("subtitle"), market.get("yes_sub_title")),
        "market_type": market.get("market_type"),
        "open_time": market.get("open_time"),
        "close_time": market.get("close_time"),
        "expected_expiration_time": market.get("expected_expiration_time"),
        "expiration_time": market.get("expiration_time"),
        "latest_expiration_time": market.get("latest_expiration_time"),
        "occurrence_datetime": _coalesce(
            market.get("occurrence_datetime"),
            market.get("occurrence_time"),
        ),
        "settlement_ts": market.get("settlement_ts"),
        "can_close_early": market.get("can_close_early"),
        "result": market.get("result"),
        "volume": _coalesce(market.get("volume_fp"), market.get("volume")),
        "open_interest": _coalesce(
            market.get("open_interest_fp"),
            market.get("open_interest"),
        ),
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "settlement_value_dollars": market.get("settlement_value_dollars"),
        "data_source": source,
    }
    return lean_usable_row(row)


def load_or_fetch_json(
    path,
    fetch_fn: Callable[[], dict],
    *,
    refresh: bool,
) -> dict | None:
    if path.exists() and not refresh:
        payload = cache.read_json(path)
        if isinstance(payload, dict) and payload.get("_missing"):
            return None
        return payload
    try:
        payload = fetch_fn()
        cache.write_json(path, payload)
        return payload
    except KalshiNotFoundError:
        cache.write_json(
            path,
            {
                "_missing": True,
                "status": 404,
            },
        )
        return None
    except KalshiAPIError:
        if path.exists():
            payload = cache.read_json(path)
            if isinstance(payload, dict) and payload.get("_missing"):
                return None
            return payload
        raise


class MarketInventory:
    def __init__(
        self,
        client: KalshiClient,
        *,
        refresh: bool = False,
        refresh_streams: set[str] | None = None,
        progress: ProgressFn | None = None,
    ) -> None:
        self.client = client
        self.refresh = refresh
        # When refresh=True and refresh_streams is set, only those streams wipe/refetch.
        self.refresh_streams = refresh_streams
        self.progress = progress or (lambda _m: None)
        cache.ensure_dirs()

    def _stream_refresh(self, name: str) -> bool:
        if not self.refresh:
            return False
        if self.refresh_streams is None:
            return True
        return name in self.refresh_streams

    def get_cutoff(self) -> dict:
        path = cache.cutoff_path()
        return load_or_fetch_json(
            path,
            self.client.get_historical_cutoff,
            refresh=self.refresh,
        ) or {}

    def fetch_current_cutoff_force_network(self) -> dict:
        """Network-only cutoff fetch; abort caller must not fall back to cache.

        On success, atomically replaces the cached cutoff file.
        """
        payload = self.client.fetch_historical_cutoff_force_network()
        cache.write_json(cache.cutoff_path(), payload)
        return payload

    def _load_pages_from_disk(self, name: str) -> tuple[list[dict], dict]:
        pages_dir = cache.inventory_pages_dir(name)
        meta = cache.read_json(cache.inventory_pages_meta_path(name), default={}) or {}
        if not pages_dir.exists():
            return [], meta
        markets: list[dict] = []
        page_files = sorted(pages_dir.glob("page_*.json"))
        for path in page_files:
            payload = cache.read_json(path, default={}) or {}
            markets.extend(payload.get("markets") or [])
        return markets, meta

    def _count_markets_on_disk(self, name: str) -> int:
        pages_dir = cache.inventory_pages_dir(name)
        if not pages_dir.exists():
            return 0
        total = 0
        for path in sorted(pages_dir.glob("page_*.json")):
            payload = cache.read_json(path, default={}) or {}
            total += len(payload.get("markets") or [])
        return total

    def iter_page_markets(
        self,
        name: str,
        *,
        max_items: int | None = None,
    ) -> Iterator[dict]:
        """Yield markets page-by-page without accumulating the full inventory."""
        yield from iter_inventory_page_markets(name, max_items=max_items)

    def get_stream_status(self, name: str) -> InventoryStreamStatus:
        meta = cache.read_json(cache.inventory_pages_meta_path(name), default={}) or {}
        count = meta.get("count")
        if count is None:
            count = self._count_markets_on_disk(name)
        return InventoryStreamStatus(
            name=name,
            pages=int(meta.get("pages") or 0),
            count=int(count or 0),
            complete=bool(meta.get("complete")),
            truncated_by_limit=bool(meta.get("truncated_by_limit")),
        )

    def _write_inventory_meta_only(self, name: str, meta: dict) -> None:
        """Persist page/stream meta without rewriting a giant aggregate jsonl."""
        cache.write_json(
            cache.inventory_meta_path(name),
            {
                "count": int(meta.get("count") or 0),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "complete": bool(meta.get("complete")),
                "pages": int(meta.get("pages") or 0),
                "truncated_by_limit": bool(meta.get("truncated_by_limit")),
            },
        )

    def _write_inventory_aggregate(self, name: str, markets: list[dict], meta: dict) -> None:
        # Avoid materializing multi-million-row aggregates in one write.
        if len(markets) > 50_000:
            meta = dict(meta)
            meta["count"] = len(markets)
            cache.write_json(cache.inventory_pages_meta_path(name), meta)
            self._write_inventory_meta_only(name, meta)
            return
        cache.write_jsonl(cache.inventory_path(name), markets)
        self._write_inventory_meta_only(name, {**meta, "count": len(markets)})

    def _apply_max_items_truncation_meta(
        self,
        name: str,
        *,
        available_count: int,
        meta: dict,
        max_items: int | None,
    ) -> tuple[int, dict]:
        """
        Persist truncation flags using counts only (no full in-memory list).

        Returns (returned_count, meta).
        """
        meta = dict(meta or {})
        if max_items is None:
            meta["truncated_by_limit"] = False
            meta["count"] = available_count
            cache.write_json(cache.inventory_pages_meta_path(name), meta)
            self._write_inventory_meta_only(name, meta)
            return available_count, meta

        truncated = available_count > max_items or (
            available_count >= max_items and not bool(meta.get("complete"))
        )
        if bool(meta.get("complete")) and available_count <= max_items:
            truncated = False
        meta["truncated_by_limit"] = truncated
        meta["count"] = available_count
        cache.write_json(cache.inventory_pages_meta_path(name), meta)
        self._write_inventory_meta_only(name, meta)
        returned = min(available_count, max_items)
        return returned, meta

    def _apply_max_items_truncation(
        self,
        name: str,
        markets: list[dict],
        meta: dict,
        *,
        max_items: int | None,
    ) -> tuple[list[dict], dict]:
        """
        Apply optional max_items to the returned list and persist truncation flags.

        ``complete`` remains the API/natural-end flag from page meta.
        ``truncated_by_limit`` is true only when the limit discarded available markets.
        """
        returned_count, meta = self._apply_max_items_truncation_meta(
            name,
            available_count=len(markets),
            meta=meta,
            max_items=max_items,
        )
        if max_items is None:
            self._write_inventory_aggregate(name, markets, meta)
            return markets, meta
        sliced = markets[:returned_count]
        if len(markets) <= 50_000:
            self._write_inventory_aggregate(name, markets, meta)
        return sliced, meta

    def _fetch_inventory_paginated(
        self,
        name: str,
        *,
        fetch_page_fn: Callable[..., list[dict]],
        max_items: int | None = None,
    ) -> list[dict]:
        """
        Resume page-cached inventory downloads.

        Each API page is written under data/cache/inventory/{name}/pages/.
        Markets are not accumulated in RAM during download.
        """
        pages_dir = cache.inventory_pages_dir(name)
        pages_dir.mkdir(parents=True, exist_ok=True)
        meta_path = cache.inventory_pages_meta_path(name)
        meta = cache.read_json(meta_path, default={}) or {}

        stream_refresh = self._stream_refresh(name)
        if stream_refresh:
            for path in pages_dir.glob("page_*.json"):
                path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            # Prevent legacy jsonl from re-seeding a wiped stream.
            legacy_path = cache.inventory_path(name)
            if legacy_path.exists():
                legacy_path.unlink(missing_ok=True)
            meta = {}

        page_files = sorted(pages_dir.glob("page_*.json")) if pages_dir.exists() else []
        if not page_files and not stream_refresh:
            legacy = cache.read_jsonl(cache.inventory_path(name))
            if legacy:
                self.progress(
                    f"Seeding {name} page cache from legacy inventory "
                    f"({len(legacy):,} markets)"
                )
                path = pages_dir / "page_0000.json"
                cache.write_json(
                    path,
                    {
                        "page_index": 0,
                        "markets": legacy,
                        "next_cursor": None,
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "seeded_from_legacy": True,
                    },
                    compact=True,
                )
                meta = {
                    "pages": 1,
                    "next_cursor": None,
                    "complete": False,
                    "count": len(legacy),
                    "seeded_from_legacy": True,
                    "resume_unsupported": True,
                    "truncated_by_limit": False,
                }
                cache.write_json(meta_path, meta)
                page_files = [path]

        if meta.get("resume_unsupported") and not stream_refresh:
            available = int(meta.get("count") or self._count_markets_on_disk(name))
            self.progress(
                f"Using legacy-seeded {name} inventory "
                f"({available:,} markets; deepen with "
                f"--inventory-stream {name.replace('_settled','')} --refresh)"
            )
            self._apply_max_items_truncation_meta(
                name, available_count=available, meta=meta, max_items=max_items
            )
            # Small legacy dumps can still be returned as a concrete list.
            markets, _ = self._load_pages_from_disk(name)
            if max_items is not None:
                return markets[:max_items]
            return markets

        available = int(meta.get("count") or 0)
        if available <= 0 and page_files:
            available = self._count_markets_on_disk(name)
            meta["count"] = available
            cache.write_json(meta_path, meta)

        if meta.get("complete") and not stream_refresh:
            if max_items is None or available >= max_items:
                self.progress(
                    f"Using page-cached {name} inventory "
                    f"({available:,} markets, complete)"
                )
                self._apply_max_items_truncation_meta(
                    name, available_count=available, meta=meta, max_items=max_items
                )
                return []

        start_page = int(meta.get("pages") or 0)
        start_cursor = meta.get("next_cursor")
        collected_count = available

        # Prefer the last successfully written page as the resume source of truth.
        # A crash during page serialization can leave meta briefly inconsistent.
        if page_files:
            last_payload = cache.read_json(page_files[-1], default={}) or {}
            last_idx = int(last_payload.get("page_index", len(page_files) - 1))
            start_page = last_idx + 1
            start_cursor = last_payload.get("next_cursor")
            if meta.get("count") is None or int(meta.get("pages") or 0) != start_page:
                collected_count = self._count_markets_on_disk(name)
                meta = {
                    **meta,
                    "pages": start_page,
                    "next_cursor": start_cursor,
                    "complete": not bool(start_cursor),
                    "count": collected_count,
                }
                cache.write_json(meta_path, meta)
                available = collected_count

        if max_items is not None and collected_count >= max_items:
            self.progress(
                f"Using page-cached {name} inventory "
                f"({collected_count:,} markets, capped at {max_items:,})"
            )
            self._apply_max_items_truncation_meta(
                name, available_count=collected_count, meta=meta, max_items=max_items
            )
            return []

        remaining = None if max_items is None else max(0, max_items - collected_count)
        running_count = collected_count

        def on_page(page_index: int, page: list[dict], next_cursor: str | None) -> None:
            nonlocal running_count
            path = pages_dir / f"page_{page_index:04d}.json"
            cache.write_json(
                path,
                {
                    "page_index": page_index,
                    "markets": page,
                    "next_cursor": next_cursor,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                },
                compact=True,
            )
            running_count += len(page)
            meta_local = {
                "pages": page_index + 1,
                "next_cursor": next_cursor,
                "complete": not bool(next_cursor),
                "count": running_count,
                "truncated_by_limit": False,
            }
            cache.write_json(meta_path, meta_local)

        self.progress(
            f"Downloading {name} markets from page {start_page}"
            + (f" (have {collected_count:,})" if collected_count else "")
        )
        fetch_page_fn(
            max_items=remaining,
            page_callback=on_page,
            start_cursor=start_cursor,
            start_page_index=start_page,
            accumulate=False,
            start_count=collected_count,
        )
        meta = cache.read_json(meta_path, default={}) or {}
        available = int(meta.get("count") or self._count_markets_on_disk(name))
        self._apply_max_items_truncation_meta(
            name, available_count=available, meta=meta, max_items=max_items
        )
        return []

    def _load_inventory_chunk(
        self,
        name: str,
        fetch_fn: Callable[[], list[dict]],
        *,
        min_cached: int | None = None,
    ) -> list[dict]:
        """Legacy single-file inventory load (tests / fallback)."""
        path = cache.inventory_path(name)
        meta_path = cache.inventory_meta_path(name)
        if path.exists() and not self.refresh:
            cached = cache.read_jsonl(path)
            if min_cached is None or len(cached) >= min_cached:
                self.progress(f"Using cached {name} inventory ({path})")
                return cached
            self.progress(
                f"Cached {name} inventory too small ({len(cached)} < {min_cached}); re-downloading"
            )

        self.progress(f"Downloading {name} markets...")
        markets = fetch_fn()
        cache.write_jsonl(path, markets)
        cache.write_json(
            meta_path,
            {
                "count": len(markets),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return markets

    def fetch_historical_markets(
        self,
        *,
        max_items: int | None = None,
        min_cached: int | None = None,
        use_page_cache: bool = True,
    ) -> list[dict]:
        if use_page_cache:
            return self._fetch_inventory_paginated(
                "historical",
                fetch_page_fn=lambda **kwargs: self.client.get_historical_markets(
                    mve_filter="exclude",
                    **kwargs,
                ),
                max_items=max_items,
            )
        return self._load_inventory_chunk(
            "historical",
            lambda: self.client.get_historical_markets(
                mve_filter="exclude",
                max_items=max_items,
            ),
            min_cached=min_cached,
        )

    def fetch_recent_settled_markets(
        self,
        *,
        max_items: int | None = None,
        min_cached: int | None = None,
        use_page_cache: bool = True,
        min_settled_ts: Any = None,
    ) -> list[dict]:
        if min_settled_ts is None:
            cutoff = self.get_cutoff()
            min_settled_ts = cutoff.get("market_settled_ts")
        # API expects unix epoch seconds for min_settled_ts.
        if min_settled_ts is not None and not isinstance(min_settled_ts, (int, float)):
            parsed = _parse_ts(min_settled_ts)
            if parsed is not None:
                min_settled_ts = int(parsed.timestamp())
        if use_page_cache:
            return self._fetch_inventory_paginated(
                "recent_settled",
                fetch_page_fn=lambda **kwargs: self.client.get_markets(
                    status="settled",
                    mve_filter="exclude",
                    min_settled_ts=min_settled_ts,
                    limit=1000,
                    **kwargs,
                ),
                max_items=max_items,
            )
        return self._load_inventory_chunk(
            "recent_settled",
            lambda: self.client.get_markets(
                status="settled",
                mve_filter="exclude",
                min_settled_ts=min_settled_ts,
                limit=1000,
                max_items=max_items,
            ),
            min_cached=min_cached,
        )

    def ensure_inventory_pages(
        self,
        *,
        inventory_max_items: int | None = None,
        streams: tuple[str, ...] = ("historical", "recent_settled"),
    ) -> tuple[InventoryStreamStatus, InventoryStreamStatus]:
        """Download/resume selected inventory streams without loading them into RAM."""
        if "historical" in streams:
            self.fetch_historical_markets(max_items=inventory_max_items)
        if "recent_settled" in streams or "recent" in streams:
            self.fetch_recent_settled_markets(max_items=inventory_max_items)
        return (
            self.get_stream_status("historical"),
            self.get_stream_status("recent_settled"),
        )

    def recent_stream_diagnostics(self) -> dict[str, Any]:
        meta = cache.read_json(
            cache.inventory_pages_meta_path("recent_settled"), default={}
        ) or {}
        status = self.get_stream_status("recent_settled")
        first_close = None
        last_close = None
        # Sample close times from first/last page only (memory-safe).
        pages_dir = cache.inventory_pages_dir("recent_settled")
        page_files = sorted(pages_dir.glob("page_*.json")) if pages_dir.exists() else []
        if page_files:
            first_page = cache.read_json(page_files[0], default={}) or {}
            last_page = cache.read_json(page_files[-1], default={}) or {}
            for market in first_page.get("markets") or []:
                ts = _parse_ts(market.get("close_time"))
                if ts is not None:
                    first_close = ts if first_close is None or ts < first_close else first_close
            for market in last_page.get("markets") or []:
                ts = _parse_ts(market.get("close_time"))
                if ts is not None:
                    last_close = ts if last_close is None or ts > last_close else last_close
        return {
            "pages_cached": status.pages,
            "markets_retrieved": status.count,
            "first_close_date": first_close.date().isoformat() if first_close else None,
            "last_close_date": last_close.date().isoformat() if last_close else None,
            "last_cursor": meta.get("next_cursor"),
            "complete": status.complete,
            "truncated_by_limit": status.truncated_by_limit,
            "resume_unsupported": bool(meta.get("resume_unsupported")),
            "seeded_from_legacy": bool(meta.get("seeded_from_legacy")),
        }

    def get_event(self, event_ticker: str) -> dict | None:
        if not event_ticker:
            return None
        path = cache.event_path(event_ticker)
        try:
            return load_or_fetch_json(
                path,
                lambda: self.client.get_event(event_ticker),
                refresh=self.refresh,
            )
        except KalshiAPIError as error:
            self.progress(f"Event fetch failed {event_ticker}: {error}")
            if path.exists():
                payload = cache.read_json(path)
                if isinstance(payload, dict) and payload.get("_missing"):
                    return None
                return payload
            return None

    def get_series(self, series_ticker: str) -> dict | None:
        if not series_ticker:
            return None
        path = cache.series_path(series_ticker)
        try:
            payload = load_or_fetch_json(
                path,
                lambda: self.client.get_series(series_ticker),
                refresh=self.refresh,
            )
            return payload
        except KalshiAPIError as error:
            self.progress(f"Series fetch failed {series_ticker}: {error}")
            if path.exists():
                cached = cache.read_json(path)
                if isinstance(cached, dict) and cached.get("_missing"):
                    return None
                return cached
            return None

    def get_fee_changes(self) -> list[dict]:
        path = cache.fee_changes_path()
        if path.exists() and not self.refresh:
            payload = cache.read_json(path, default={"series_fee_change_arr": []})
            return payload.get("series_fee_change_arr") or []

        try:
            changes = self.client.get_fee_changes(show_historical=True)
            cache.write_json(path, {"series_fee_change_arr": changes})
            return changes
        except KalshiAPIError as error:
            self.progress(f"Fee changes fetch failed: {error}")
            if path.exists():
                payload = cache.read_json(path, default={"series_fee_change_arr": []})
                return payload.get("series_fee_change_arr") or []
            return []

    def _can_reconcile_from_cache(
        self,
        historical: InventoryStreamStatus,
        recent: InventoryStreamStatus,
    ) -> bool:
        if self.refresh:
            return False
        if not cache.usable_inventory_path().exists():
            return False
        return stream_complete_inventory(historical, recent)

    def _lookup_category(
        self,
        market: dict,
        series_cache: dict[str, str | None],
        event_cache: dict[str, str | None],
    ) -> str:
        series_ticker = series_ticker_from_market(market)
        event_ticker = market.get("event_ticker")
        category: str | None = None
        if series_ticker:
            if series_ticker not in series_cache:
                series = self.get_series(series_ticker)
                series_cache[series_ticker] = (
                    (series or {}).get("category") if series else None
                )
            category = series_cache.get(series_ticker)
        if not category and event_ticker:
            if event_ticker not in event_cache:
                event = self.get_event(event_ticker)
                event_cache[str(event_ticker)] = (
                    (event or {}).get("category") if event else None
                )
            category = event_cache.get(str(event_ticker))
        return category or "Unknown"

    def _finalize_combined_inventory(
        self,
        audit: InventoryAudit,
        *,
        extra_diagnostics: dict[str, Any] | None = None,
    ) -> tuple[list[dict], InventoryAudit]:
        self.progress("Streaming combined inventory diagnostics...")
        diag = stream_combined_inventory_diagnostics(
            cache.usable_inventory_path(),
            progress=self.progress,
        )
        self.progress("Classifying cached raw pages for unique/exclusion counts...")
        recon = classify_cached_inventory_pages(progress=self.progress)

        audit.combined_unique = int(recon["combined_unique"])
        audit.usable = int(diag["usable_count"])
        audit.skip_reasons = Counter(recon.get("skip_reasons") or {})
        audit.excluded_combos = audit.skip_reasons.get("excluded_combo", 0)
        audit.missing_close_time = audit.skip_reasons.get("missing_close_time", 0)
        audit.missing_result = audit.skip_reasons.get("missing_result", 0)
        audit.nonstandard_settlement = audit.skip_reasons.get(
            "nonstandard_settlement", 0
        )
        audit.unique_excluded = int(recon["unique_excluded"])
        audit.categories = Counter(diag.get("inventory_categories") or {})

        recon_valid = counts_reconcile(audit)
        if audit.usable != int(recon["usable_unique"]):
            recon_valid = False

        sources = set(diag.get("data_sources_seen") or [])
        sample_valid = bool(diag.get("required_sample_valid"))
        if audit.historical_count > 0 and audit.recent_count > 0:
            if sources != {"historical", "recent"}:
                sample_valid = False

        diag["count_reconciliation_valid"] = recon_valid
        diag["unique_excluded"] = audit.unique_excluded
        diag["unique_excluded_by_reason"] = dict(audit.skip_reasons)
        diag["required_sample_valid"] = sample_valid
        diag["usable_unique_from_pages"] = int(recon["usable_unique"])
        if extra_diagnostics:
            diag.update(extra_diagnostics)

        valid, errors = evaluate_inventory_integrity(
            usable_count=audit.usable,
            date_min=diag.get("date_min"),
            date_max=diag.get("date_max"),
            years=list(diag.get("inventory_years") or []),
            unique_events=int(diag.get("inventory_unique_events") or 0),
            unique_series=int(diag.get("inventory_unique_series") or 0),
            count_reconciliation_valid=recon_valid,
            required_field_missing_counts=diag.get("required_field_missing_counts"),
            required_sample_valid=sample_valid,
        )
        diag["inventory_integrity_valid"] = valid
        diag["inventory_integrity_errors"] = errors
        diag["inventory_schema_version"] = INVENTORY_SCHEMA_VERSION
        audit.inventory_integrity_valid = valid
        audit._stream_diagnostics = diag  # type: ignore[attr-defined]
        self.progress(
            f"Combined inventory: unique={audit.combined_unique:,} "
            f"excluded={audit.unique_excluded:,} usable={audit.usable:,} "
            f"reconciliation={'valid' if recon_valid else 'INVALID'} "
            f"integrity={'valid' if valid else 'INVALID'}"
        )
        if errors:
            for error in errors:
                self.progress(f"Inventory integrity error: {error}")
        return [], audit

    def _reconcile_from_cache(
        self,
        *,
        cutoff: dict,
    ) -> tuple[list[dict], InventoryAudit]:
        historical_status = self.get_stream_status("historical")
        recent_status = self.get_stream_status("recent_settled")
        audit = InventoryAudit(
            historical_count=historical_status.count,
            recent_count=recent_status.count,
            cutoff=cutoff,
        )
        recent_path = cache.usable_recent_path()
        if recent_path.exists():
            self.progress(
                "Re-merging combined usable inventory from cached lean files "
                "(preserving existing data_source)..."
            )
            merge_stats = stream_merge_usable_inventory(
                existing_path=cache.usable_inventory_path(),
                recent_path=recent_path,
            )
            self.progress(
                f"Merge kept={merge_stats['kept_existing']:,} "
                f"recent={merge_stats['recent_rows']:,}"
            )
            extra = {"recent_only_merge": merge_stats, "cache_only_reconcile": True}
        else:
            self.progress(
                "No usable_recent.jsonl; streaming existing combined inventory "
                "through the canonical normalizer..."
            )
            _rewrite_usable_through_normalizer(cache.usable_inventory_path())
            extra = {"cache_only_reconcile": True}
        return self._finalize_combined_inventory(audit, extra_diagnostics=extra)

    def _build_recent_only(
        self,
        *,
        inventory_max_items: int | None,
        enrich_limit: int | None,
        cutoff: dict,
    ) -> tuple[list[dict], InventoryAudit]:
        """Refresh/enrich recent stream and merge into usable inventory without loading 11M rows."""
        meta = cache.read_json(
            cache.inventory_pages_meta_path("recent_settled"), default={}
        ) or {}
        if meta.get("resume_unsupported") and not self._stream_refresh("recent_settled"):
            raise RuntimeError(
                "recent_settled is a legacy non-resumable seed. "
                "Re-run with: python efficiency_backtest.py --inventory-only "
                "--inventory-stream recent --refresh"
            )

        self.fetch_recent_settled_markets(max_items=inventory_max_items)
        recent_status = self.get_stream_status("recent_settled")
        historical_status = self.get_stream_status("historical")

        audit = InventoryAudit(
            historical_count=historical_status.count,
            recent_count=recent_status.count,
            combined_unique=0,
            cutoff=cutoff,
        )

        can_skip_enrich = (
            not self.refresh
            and cache.usable_recent_path().exists()
            and cache.usable_inventory_path().exists()
            and recent_status.complete
            and not recent_status.truncated_by_limit
        )
        if can_skip_enrich:
            self.progress(
                "Recent page cache complete; skipping re-enrich and reconciling "
                "from cached lean files."
            )
            return self._reconcile_from_cache(cutoff=cutoff)

        cache.ensure_dirs()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(cache.usable_recent_path().parent),
            prefix=".usable_recent.",
            suffix=".jsonl.tmp",
        )
        tmp_path = Path(tmp_name)
        series_cache: dict[str, str | None] = {}
        event_cache: dict[str, str | None] = {}
        seen: set[str] = set()
        enriched = 0
        scanned = 0
        started_at = time.monotonic()

        self.progress(
            f"Enriching recent_settled only ({recent_status.count:,} markets)..."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for market in self.iter_page_markets(
                    "recent_settled", max_items=inventory_max_items
                ):
                    scanned += 1
                    ticker = market.get("ticker")
                    if not ticker or ticker in seen:
                        continue
                    seen.add(str(ticker))
                    if enrich_limit is not None and enriched >= enrich_limit:
                        break
                    enriched += 1

                    skip = inventory_skip_reason(market)
                    if skip:
                        record_inventory_skip(audit, skip)
                        continue

                    category = self._lookup_category(market, series_cache, event_cache)
                    lean = normalize_inventory_market(
                        market,
                        data_source="recent",
                        category=category,
                    )
                    handle.write(json.dumps(lean, sort_keys=True) + "\n")
                    audit.categories[category] += 1
                    audit.usable += 1
                    if scanned % 25_000 == 0:
                        self.progress(
                            f"Recent enrich: scanned={scanned:,} usable={audit.usable:,}"
                        )

            os.replace(tmp_path, cache.usable_recent_path())
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        merge_stats = stream_merge_usable_inventory(
            existing_path=cache.usable_inventory_path(),
            recent_path=cache.usable_recent_path(),
        )
        self.progress(
            f"Merged recent usable into inventory "
            f"(kept={merge_stats['kept_existing']:,}, "
            f"recent={merge_stats['recent_rows']:,}) "
            f"in {_format_duration(time.monotonic() - started_at)}"
        )
        return self._finalize_combined_inventory(
            audit,
            extra_diagnostics={"recent_only_merge": merge_stats},
        )

    def build(
        self,
        *,
        max_markets: int | None = None,
        enrich_limit: int | None = None,
        prefer_long_lived: bool = False,
        inventory_max_items: int | None = None,
        keep_in_memory: bool | None = None,
        streams: tuple[str, ...] = ("historical", "recent_settled"),
    ) -> tuple[list[dict], InventoryAudit]:
        """
        Build enriched usable inventory.

        Streams page-cached markets so multi-million inventories do not need to
        reside fully in RAM. Usable rows are written atomically to
        ``usable_inventory.jsonl`` during enrichment.

        ``keep_in_memory`` defaults to True for small inventories and False when
        either stream exceeds 50k markets (returns an empty list; read from disk).
        """
        del prefer_long_lived  # selection moved to research.sampling
        del max_markets  # callers sample via research.sampling
        cutoff = self.get_cutoff()

        normalized = tuple(
            "recent_settled" if s == "recent" else s for s in streams
        )
        if normalized == ("recent_settled",):
            return self._build_recent_only(
                inventory_max_items=inventory_max_items,
                enrich_limit=enrich_limit,
                cutoff=cutoff,
            )

        historical_status, recent_status = self.ensure_inventory_pages(
            inventory_max_items=inventory_max_items,
            streams=normalized,
        )
        if self._can_reconcile_from_cache(historical_status, recent_status):
            self.progress(
                "Page caches complete; reconciling combined inventory from "
                "existing cache (zero inventory downloads)."
            )
            return self._reconcile_from_cache(cutoff=cutoff)

        historical_count = historical_status.count
        recent_count = recent_status.count
        if keep_in_memory is None:
            keep_in_memory = (historical_count + recent_count) <= 50_000

        audit = InventoryAudit(
            historical_count=historical_count,
            recent_count=recent_count,
            combined_unique=0,
            cutoff=cutoff,
        )

        series_cache: dict[str, dict | None] = {}
        event_cache: dict[str, dict | None] = {}
        seen: set[str] = set()
        usable: list[dict] = []
        identity_digests: list[bytes] = []
        by_year: Counter[int] = Counter()
        by_event: Counter[str] = Counter()
        by_series: Counter[str] = Counter()
        by_day: Counter[str] = Counter()
        series_set: set[str] = set()
        event_set: set[str] = set()
        date_min = None
        date_max = None

        cache.ensure_dirs()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(cache.usable_inventory_path().parent),
            prefix=".usable_inventory.",
            suffix=".jsonl.tmp",
        )
        tmp_path = Path(tmp_name)
        enriched = 0
        scanned = 0
        total_estimate = max(1, historical_count + recent_count)
        started_at = time.monotonic()
        last_progress_at = started_at
        progress_every = 25_000

        self.progress(
            f"Enriching inventory over ~{total_estimate:,} cached markets "
            f"(historical={historical_count:,}, recent={recent_count:,})..."
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                def report_progress(*, force: bool = False) -> None:
                    nonlocal last_progress_at
                    now = time.monotonic()
                    if (
                        not force
                        and scanned % progress_every != 0
                        and (now - last_progress_at) < 15
                    ):
                        return
                    last_progress_at = now
                    elapsed = max(0.001, now - started_at)
                    rate = scanned / elapsed
                    pct = min(100.0, 100.0 * scanned / total_estimate)
                    remaining = max(0, total_estimate - scanned)
                    eta = remaining / rate if rate > 0 else None
                    self.progress(
                        f"Enriching inventory: {scanned:,}/{total_estimate:,} "
                        f"({pct:.1f}%) | unique={len(seen):,} | "
                        f"usable={audit.usable:,} | {rate:,.0f}/s | "
                        f"ETA {_format_duration(eta)}"
                    )

                def consume(market: dict, *, data_source_hint: str) -> None:
                    nonlocal enriched, date_min, date_max, scanned
                    scanned += 1
                    report_progress()
                    ticker = market.get("ticker")
                    if not ticker or ticker in seen:
                        return
                    seen.add(ticker)

                    if enrich_limit is not None and enriched >= enrich_limit:
                        return
                    enriched += 1

                    skip = inventory_skip_reason(market)
                    if skip:
                        record_inventory_skip(audit, skip)
                        return

                    series_ticker = series_ticker_from_market(market)
                    event_ticker = market.get("event_ticker")

                    if series_ticker and series_ticker not in series_cache:
                        series_cache[series_ticker] = self.get_series(series_ticker)
                    if event_ticker and event_ticker not in event_cache:
                        event_cache[event_ticker] = self.get_event(event_ticker)

                    series = series_cache.get(series_ticker) if series_ticker else None
                    event = event_cache.get(event_ticker) if event_ticker else None
                    category = None
                    if series:
                        category = series.get("category")
                    if not category and event:
                        category = event.get("category")
                    category = category or "Unknown"

                    data_source = (
                        "historical"
                        if _settled_before_cutoff(market, cutoff)
                        else data_source_hint
                    )
                    lean = normalize_inventory_market(
                        market,
                        data_source=data_source,
                        category=category,
                    )
                    handle.write(json.dumps(lean, sort_keys=True) + "\n")
                    identity_digests.append(identity_digest(lean))
                    audit.categories[category] += 1
                    audit.usable += 1

                    if series_ticker:
                        series_set.add(str(series_ticker))
                        by_series[str(series_ticker)] += 1
                    if event_ticker:
                        event_set.add(str(event_ticker))
                        by_event[str(event_ticker)] += 1
                    close = market.get("close_time") or market.get("settlement_ts")
                    ts = _parse_ts(close)
                    if ts is not None:
                        by_year[ts.year] += 1
                        day = ts.date().isoformat()
                        by_day[day] += 1
                        if date_min is None or ts < date_min:
                            date_min = ts
                        if date_max is None or ts > date_max:
                            date_max = ts

                    if keep_in_memory:
                        row = dict(lean)
                        row["event"] = event
                        row["series"] = series
                        row["raw_market"] = market
                        usable.append(row)

                # Historical first so dedupe prefers historical rows.
                for market in self.iter_page_markets(
                    "historical", max_items=inventory_max_items
                ):
                    consume(market, data_source_hint="historical")
                for market in self.iter_page_markets(
                    "recent_settled", max_items=inventory_max_items
                ):
                    consume(market, data_source_hint="recent")
                report_progress(force=True)

            write_usable_inventory_from_path(tmp_path)
            self.progress(
                f"Enrichment complete: scanned={scanned:,} unique={len(seen):,} "
                f"usable={audit.usable:,} in {_format_duration(time.monotonic() - started_at)}"
            )
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        audit.combined_unique = len(seen)
        audit.unique_excluded = unique_excluded_count(audit)
        date_min_s = date_min.date().isoformat() if date_min else None
        date_max_s = date_max.date().isoformat() if date_max else None
        recon_valid = counts_reconcile(audit)
        required_missing, optional_missing = count_missing_fields(usable) if usable else ({}, {})
        sample_valid = (
            all(required_research_fields_ok(row) for row in usable) if usable else True
        )
        # Stash streaming diagnostics for snapshot/reporting without reloading.
        audit._stream_diagnostics = {  # type: ignore[attr-defined]
            "inventory_markets": audit.usable,
            "inventory_date_range": (
                f"{date_min_s} -> {date_max_s}"
                if date_min_s and date_max_s
                else "n/a"
            ),
            "date_min": date_min_s,
            "date_max": date_max_s,
            "inventory_years": sorted(by_year),
            "inventory_categories": dict(audit.categories.most_common()),
            "inventory_unique_series": len(series_set),
            "inventory_unique_events": len(event_set),
            "markets_by_year": dict(sorted(by_year.items())),
            "largest_event": (
                by_event.most_common(1)[0] if by_event else ("n/a", 0)
            ),
            "largest_series": (
                by_series.most_common(1)[0] if by_series else ("n/a", 0)
            ),
            "largest_day": (by_day.most_common(1)[0] if by_day else ("n/a", 0)),
            "inventory_hash": compute_inventory_hash_from_digests(identity_digests),
            "count_reconciliation_valid": recon_valid,
            "unique_excluded": audit.unique_excluded,
            "required_field_missing_counts": required_missing,
            "optional_field_missing_counts": optional_missing,
            "required_sample_valid": sample_valid,
        }
        return usable, audit


def _rewrite_usable_through_normalizer(path: Path) -> None:
    """Stream an existing combined JSONL through the canonical normalizer."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".usable_inventory.normalize.",
        suffix=".jsonl.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out, path.open(
            "r", encoding="utf-8"
        ) as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                row = json.loads(raw)
                lean = normalize_inventory_market(row)
                out.write(json.dumps(lean, sort_keys=True) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class MissingSettlementTsError(ValueError):
    """settlement_ts is required for candle endpoint routing; do not guess."""


def _settled_before_cutoff(market: dict, cutoff: dict) -> bool:
    """True iff settlement_ts < market_settled_ts (strict).

    Boundary:
      settlement < T  -> historical (True)
      settlement == T -> live (False)
      settlement > T  -> live (False)
    """
    settled = _parse_ts(market.get("settlement_ts"))
    if settled is None:
        raise MissingSettlementTsError(
            "Missing settlement_ts; cannot route candle endpoint"
        )
    cutoff_ts = _parse_ts(cutoff.get("market_settled_ts"))
    if cutoff_ts is None:
        raise MissingSettlementTsError(
            "Missing market_settled_ts on cutoff; cannot route candle endpoint"
        )
    return settled < cutoff_ts


def market_uses_historical_candles(market_row: dict, cutoff: dict) -> bool:
    """Route by settlement_ts vs current cutoff only.

    Does not use close_time, cached data_source, or inventory-build cutoff alone.
    """
    market = market_row.get("raw_market") or market_row
    # Prefer top-level settlement_ts when present (lean inventory rows).
    if market_row.get("settlement_ts"):
        market = market_row
    return _settled_before_cutoff(market, cutoff)


def candle_endpoint_for_market(market_row: dict, cutoff: dict) -> tuple[bool, str]:
    """Return (use_historical, endpoint_path)."""
    ticker = str(market_row.get("ticker") or "")
    series = str(market_row.get("series_ticker") or "")
    use_historical = market_uses_historical_candles(market_row, cutoff)
    if use_historical:
        return True, f"/historical/markets/{ticker}/candlesticks"
    return False, f"/series/{series}/markets/{ticker}/candlesticks"


def _market_lifetime_hours(market: dict) -> float:
    open_ts = _parse_ts(market.get("open_time"))
    close_ts = _parse_ts(market.get("close_time"))
    if open_ts is None or close_ts is None:
        return 0.0
    return max(0.0, (close_ts - open_ts).total_seconds() / 3600.0)


def _prefer_long_lived_markets(markets: list[dict], limit: int) -> list[dict]:
    """Prefer markets open long enough for multi-horizon candle coverage."""
    ranked = sorted(
        markets,
        key=lambda m: (_market_lifetime_hours(m), float(m.get("volume_fp") or m.get("volume") or 0)),
        reverse=True,
    )
    return ranked[:limit]
