"""Frozen sample identity: hash, manifest, and inventory reload without resampling."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from research.sampling import close_date_key, close_year, iter_jsonl


class SampleIdentityError(ValueError):
    """Frozen sample does not match the expected identity."""


def compute_sample_hash(tickers: Iterable[str]) -> str:
    """SHA-256 over sorted unique ticker strings (order-independent)."""
    ordered = sorted({str(t) for t in tickers if t})
    payload = "\n".join(ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _year_counts(markets: list[dict]) -> dict[str, int]:
    counts = Counter(str(y) for y in (close_year(m) for m in markets) if y)
    return dict(sorted(counts.items()))


def _category_counts(markets: list[dict]) -> dict[str, int]:
    return dict(
        Counter(str(m.get("category") or "Unknown") for m in markets).most_common()
    )


def _date_range(markets: list[dict]) -> str:
    dates = sorted(
        d for d in (close_date_key(m) for m in markets) if d and d != "unknown"
    )
    if not dates:
        return "n/a"
    return f"{dates[0]} -> {dates[-1]}"


def sample_population_diagnostics(markets: list[dict]) -> dict[str, Any]:
    """Diagnostics for a selected market list (not observation survivors)."""
    events = {m.get("event_ticker") for m in markets if m.get("event_ticker")}
    series = {m.get("series_ticker") for m in markets if m.get("series_ticker")}
    years = sorted({y for y in (close_year(m) for m in markets) if y})
    cats = sorted({str(m.get("category") or "Unknown") for m in markets})
    return {
        "markets": len(markets),
        "events": len(events),
        "series": len(series),
        "categories": len(cats),
        "category_counts": _category_counts(markets),
        "years": years,
        "year_counts": _year_counts(markets),
        "date_range": _date_range(markets),
    }


def observation_population_diagnostics(observations: list[dict]) -> dict[str, Any]:
    """Diagnostics for usable observation rows only."""
    markets = {o.get("ticker") for o in observations if o.get("ticker")}
    events = {o.get("event_ticker") for o in observations if o.get("event_ticker")}
    series = {o.get("series_ticker") for o in observations if o.get("series_ticker")}
    years = sorted({y for y in (close_year(o) for o in observations) if y})
    cats = sorted({str(o.get("category") or "Unknown") for o in observations})
    dates = sorted(
        d for d in (close_date_key(o) for o in observations) if d and d != "unknown"
    )
    date_range = f"{dates[0]} -> {dates[-1]}" if dates else "n/a"
    return {
        "observations": len(observations),
        "unique_markets": len(markets),
        "events": len(events),
        "series": len(series),
        "categories": len(cats),
        "category_counts": dict(
            Counter(str(o.get("category") or "Unknown") for o in observations).most_common()
        ),
        "years": years,
        "year_counts": dict(
            sorted(
                Counter(
                    str(y) for y in (close_year(o) for o in observations) if y
                ).items()
            )
        ),
        "date_range": date_range,
    }


def build_sample_identity_manifest(
    *,
    markets: list[dict],
    sampling_mode: str,
    seed: int,
    sample_size: int,
    inventory_hash: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tickers = [str(m.get("ticker")) for m in markets if m.get("ticker")]
    pop = sample_population_diagnostics(markets)
    manifest: dict[str, Any] = {
        "sampling_mode": sampling_mode,
        "seed": seed,
        "sample_size": sample_size,
        "sample_hash": compute_sample_hash(tickers),
        "inventory_hash": inventory_hash,
        "selected_ticker_count": len(tickers),
        "unique_events": pop["events"],
        "unique_series": pop["series"],
        "year_counts": pop["year_counts"],
        "category_counts": pop["category_counts"],
        "years": pop["years"],
        "date_range": pop["date_range"],
        "tickers": tickers,
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def sample_identity_path(sampling_mode: str) -> Path:
    from kalshi import cache

    return cache.RESULTS_ROOT / f"sample_identity_{sampling_mode}.json"


def load_preview_tickers(preview_path: Path) -> tuple[list[str], dict]:
    if not preview_path.exists():
        raise SampleIdentityError(f"Preview not found: {preview_path}")
    payload = json.loads(preview_path.read_text(encoding="utf-8"))
    tickers = [str(t) for t in (payload.get("tickers") or []) if t]
    sampling = payload.get("sampling") or {}
    return tickers, sampling


def freeze_from_preview(
    *,
    preview_path: Path,
    inventory_hash: str | None = None,
    sampling_mode: str | None = None,
) -> dict[str, Any]:
    """Build identity manifest from an existing sample preview file."""
    tickers, sampling = load_preview_tickers(preview_path)
    mode = sampling_mode or str(sampling.get("sampling_mode") or "balanced")
    # Lightweight stand-ins so year/category counts prefer preview diagnostics.
    markets = [{"ticker": t} for t in tickers]
    year_counts = {
        str(k): int(v) for k, v in (sampling.get("selected_by_year") or {}).items()
    }
    category_counts = {
        str(k): int(v) for k, v in (sampling.get("selected_by_category") or {}).items()
    }
    years = sorted(int(y) for y in year_counts.keys())
    manifest = {
        "sampling_mode": mode,
        "seed": int(sampling.get("seed") or 42),
        "sample_size": int(sampling.get("selected_markets") or len(tickers)),
        "sample_hash": compute_sample_hash(tickers),
        "inventory_hash": inventory_hash,
        "selected_ticker_count": len(tickers),
        "unique_events": int(sampling.get("selected_unique_events") or 0),
        "unique_series": int(sampling.get("selected_unique_series") or 0),
        "year_counts": year_counts,
        "category_counts": category_counts,
        "years": years,
        "date_range": sampling.get("selected_date_range") or "n/a",
        "tickers": tickers,
        "source_preview": str(preview_path),
    }
    # markets unused beyond tickers; keep lint quiet
    _ = markets
    return manifest


def resolve_markets_by_tickers(
    inventory_path: Path,
    tickers: list[str],
) -> list[dict]:
    """Load full inventory rows for an ordered ticker list."""
    wanted = set(tickers)
    found: dict[str, dict] = {}
    for row in iter_jsonl(inventory_path):
        ticker = row.get("ticker")
        if ticker and str(ticker) in wanted:
            found[str(ticker)] = row
            if len(found) >= len(wanted):
                break
    missing = [t for t in tickers if t not in found]
    if missing:
        raise SampleIdentityError(
            f"Frozen sample missing {len(missing)} inventory rows "
            f"(e.g. {missing[:5]})"
        )
    return [found[t] for t in tickers]


def load_frozen_sample(
    manifest_path: Path,
    inventory_path: Path,
    *,
    expected_hash: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Load markets from a frozen identity manifest; fail on hash mismatch."""
    if not manifest_path.exists():
        raise SampleIdentityError(f"Frozen sample manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tickers = [str(t) for t in (manifest.get("tickers") or []) if t]
    actual_hash = compute_sample_hash(tickers)
    stored_hash = manifest.get("sample_hash")
    if stored_hash and stored_hash != actual_hash:
        raise SampleIdentityError(
            f"Manifest sample_hash mismatch: stored={stored_hash} actual={actual_hash}"
        )
    if expected_hash is not None and actual_hash != expected_hash:
        raise SampleIdentityError(
            f"Frozen sample hash does not match preview: "
            f"expected={expected_hash} actual={actual_hash}"
        )
    markets = resolve_markets_by_tickers(inventory_path, tickers)
    # Recompute identity fields from resolved rows for the FROZEN SAMPLE print.
    pop = sample_population_diagnostics(markets)
    verified = dict(manifest)
    verified["sample_hash"] = actual_hash
    verified["selected_ticker_count"] = len(markets)
    verified["unique_events"] = pop["events"]
    verified["unique_series"] = pop["series"]
    verified["year_counts"] = pop["year_counts"]
    verified["category_counts"] = pop["category_counts"]
    verified["years"] = pop["years"]
    verified["date_range"] = pop["date_range"]
    return markets, verified


def format_frozen_sample_banner(manifest: dict[str, Any]) -> str:
    years = manifest.get("year_counts") or {}
    cats = manifest.get("category_counts") or {}
    years_txt = ", ".join(f"{y}:{n}" for y, n in years.items()) or "n/a"
    cats_txt = ", ".join(f"{c}:{n}" for c, n in list(cats.items())[:8])
    if len(cats) > 8:
        cats_txt += ", ..."
    return (
        "FROZEN SAMPLE\n"
        f"markets:       {manifest.get('selected_ticker_count', 0)}\n"
        f"events:        {manifest.get('unique_events', 0)}\n"
        f"series:        {manifest.get('unique_series', 0)}\n"
        f"years:         {years_txt}\n"
        f"categories:    {cats_txt}\n"
        f"sample hash:   {manifest.get('sample_hash', 'n/a')}"
    )
