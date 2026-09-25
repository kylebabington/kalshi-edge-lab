"""KXHIGHNY CLINYC / Kalshi settlement-temperature recovery.

Preferred underlying observation: market ``expiration_value`` considered
for settlement. Values are validated at the EVENT level — all non-empty
parsed numerics must agree — before acceptance.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kalshi import cache
from kalshi.client import KalshiClient
from research.weather.models import (
    REGIME_UNKNOWN,
    SERIES_TICKER,
)
from research.weather.resolution import (
    detect_settlement_source_regime,
    get_event_date,
    group_markets_by_event,
)


CLINYC_CACHE_DIR = cache.CACHE_ROOT / "weather" / "clinyc"
SETTLED_MARKETS_CACHE = CLINYC_CACHE_DIR / "kxhighny_settled_markets.json"
FINAL_MARKET_STATUSES = frozenset({"finalized", "determined", "settled"})
RESOLUTION_OK = "ok"
RESOLUTION_CONFLICTING = "conflicting_expiration_values"
RESOLUTION_MISSING = "missing_expiration_value"
RESOLUTION_MALFORMED = "malformed_expiration_value"
RESOLUTION_NOT_FINAL = "not_final"
RESOLUTION_NO_MARKETS = "no_markets"


@dataclass
class ExpirationValueAudit:
    """Event-level expiration_value agreement check."""

    market_count: int
    expiration_values_seen: list[str]
    normalized_expiration_value: float | None
    agreement: bool
    resolution_status: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SettlementObservation:
    """Recovered Kalshi settlement temperature for one KXHIGHNY event."""

    event_ticker: str
    target_date: str | None
    settlement_temperature_f: float | None
    settlement_source_regime: str
    source_url: str | None
    retrieved_at: str
    retrieval_method: str
    raw_cache_path: str | None
    parsing_confidence: str
    warnings: list[str] = field(default_factory=list)
    resolution_status: str = RESOLUTION_OK
    expiration_audit: ExpirationValueAudit | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.expiration_audit is not None:
            payload["expiration_audit"] = self.expiration_audit.to_dict()
        return payload


def clinyc_cache_dir() -> Path:
    cache.ensure_dirs()
    CLINYC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CLINYC_CACHE_DIR


def observation_cache_path(event_ticker: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", event_ticker)
    return clinyc_cache_dir() / f"{safe}.json"


def normalize_expiration_value(raw: Any) -> float | None:
    """Parse and normalize equivalent numeric strings (84 / 84.0 / 84.00)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Strip common degree / unit suffixes without inventing values.
    text = re.sub(r"[°\s]*[Ff]?$", "", text).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not (value == value):  # NaN
        return None
    return value


def _market_is_final(market: dict[str, Any]) -> bool:
    status = str(market.get("status") or "").lower()
    if status in FINAL_MARKET_STATUSES:
        return True
    # Settled finals also expose yes/no result + settlement_ts.
    result = str(market.get("result") or "").lower()
    if result in {"yes", "no"} and market.get("settlement_ts"):
        return True
    return False


def audit_expiration_values(markets: list[dict[str, Any]]) -> ExpirationValueAudit:
    """Require unanimous numeric expiration_value across an event's markets."""
    warnings: list[str] = []
    if not markets:
        return ExpirationValueAudit(
            market_count=0,
            expiration_values_seen=[],
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_NO_MARKETS,
            warnings=["no markets provided"],
        )

    non_final = [m for m in markets if not _market_is_final(m)]
    if non_final:
        return ExpirationValueAudit(
            market_count=len(markets),
            expiration_values_seen=[],
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_NOT_FINAL,
            warnings=[
                f"{len(non_final)} market(s) not in final settled/determined state"
            ],
        )

    seen_raw: list[str] = []
    parsed: list[float] = []
    malformed = 0
    for market in markets:
        raw = market.get("expiration_value")
        if raw is None or str(raw).strip() == "":
            continue
        raw_s = str(raw).strip()
        seen_raw.append(raw_s)
        value = normalize_expiration_value(raw_s)
        if value is None:
            malformed += 1
            warnings.append(f"malformed expiration_value={raw_s!r}")
        else:
            parsed.append(value)

    if malformed and not parsed:
        return ExpirationValueAudit(
            market_count=len(markets),
            expiration_values_seen=seen_raw,
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_MALFORMED,
            warnings=warnings or ["all expiration_value entries malformed"],
        )

    if not parsed:
        return ExpirationValueAudit(
            market_count=len(markets),
            expiration_values_seen=seen_raw,
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_MISSING,
            warnings=warnings + ["no non-empty expiration_value on finalized markets"],
        )

    # Unique by exact float equality after float() parse (84 / 84.0 / 84.00 agree).
    unique = {v for v in parsed}
    if len(unique) != 1:
        return ExpirationValueAudit(
            market_count=len(markets),
            expiration_values_seen=seen_raw,
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_CONFLICTING,
            warnings=warnings
            + [f"conflicting normalized expiration values: {sorted(unique)}"],
        )

    if malformed:
        # Some rows malformed while others agree — refuse rather than invent.
        return ExpirationValueAudit(
            market_count=len(markets),
            expiration_values_seen=seen_raw,
            normalized_expiration_value=None,
            agreement=False,
            resolution_status=RESOLUTION_MALFORMED,
            warnings=warnings
            + ["refusing observation because some expiration_value rows are malformed"],
        )

    agreed = next(iter(unique))
    return ExpirationValueAudit(
        market_count=len(markets),
        expiration_values_seen=seen_raw,
        normalized_expiration_value=agreed,
        agreement=True,
        resolution_status=RESOLUTION_OK,
        warnings=warnings,
    )


def _regime_from_markets(
    markets: list[dict[str, Any]],
    *,
    event_meta: dict[str, Any] | None = None,
    series_meta: dict[str, Any] | None = None,
) -> str:
    sample = markets[0] if markets else {}
    rules = str(sample.get("rules_primary") or "")
    event_sources = None
    if event_meta:
        event_sources = event_meta.get("settlement_sources")
    series_sources = None
    if series_meta:
        series_sources = series_meta.get("settlement_sources")
    regime, _, _ = detect_settlement_source_regime(
        rules_primary=rules,
        settlement_sources=event_sources if isinstance(event_sources, list) else None,
        series_settlement_sources=(
            series_sources if isinstance(series_sources, list) else None
        ),
    )
    return regime


def fetch_settled_kxhighny_markets(
    *,
    client: KalshiClient | None = None,
    force_refresh: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Series-scoped settled markets only — never rebuilds full inventory."""
    clinyc_cache_dir()
    log = progress or (lambda _m: None)
    if SETTLED_MARKETS_CACHE.exists() and not force_refresh:
        cached = cache.read_json(SETTLED_MARKETS_CACHE, default={})
        markets = cached.get("markets") if isinstance(cached, dict) else None
        if isinstance(markets, list) and markets:
            log(f"Loaded {len(markets)} cached settled KXHIGHNY markets")
            return markets

    client = client or KalshiClient()
    log("Fetching settled KXHIGHNY markets (series-scoped)...")
    markets = client.get_markets(
        status="settled",
        series_ticker=SERIES_TICKER,
        mve_filter=None,
    )
    # Historical tier may still hold older settled rows after cutoff.
    try:
        historical = client.get_historical_markets(
            series_ticker=SERIES_TICKER,
            mve_filter=None,
        )
    except Exception as error:  # noqa: BLE001
        log(f"Historical KXHIGHNY fetch skipped: {error}")
        historical = []

    by_ticker: dict[str, dict[str, Any]] = {}
    for market in list(historical) + list(markets):
        ticker = market.get("ticker")
        if ticker:
            by_ticker[str(ticker)] = market
    merged = list(by_ticker.values())
    cache.write_json(
        SETTLED_MARKETS_CACHE,
        {
            "series_ticker": SERIES_TICKER,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(merged),
            "markets": merged,
        },
    )
    log(f"Cached {len(merged)} settled KXHIGHNY markets -> {SETTLED_MARKETS_CACHE}")
    return merged


def load_markets_for_event(
    event_ticker: str,
    *,
    client: KalshiClient | None = None,
    settled_markets: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Return (markets, event_meta, retrieval_method)."""
    client = client or KalshiClient()

    # 1) Series-scoped settled cache / fetch
    pool = settled_markets
    if pool is None:
        if SETTLED_MARKETS_CACHE.exists():
            cached = cache.read_json(SETTLED_MARKETS_CACHE, default={})
            pool = cached.get("markets") if isinstance(cached, dict) else None
    if isinstance(pool, list):
        grouped = group_markets_by_event(pool)
        if event_ticker in grouped:
            return grouped[event_ticker], None, "series_settled_cache"

    # 2) Event endpoint with nested markets
    try:
        event = client.get_event(event_ticker, with_nested_markets=True)
        markets = event.get("markets") if isinstance(event, dict) else None
        if isinstance(markets, list) and markets:
            return markets, event, "event_nested_markets"
    except Exception:  # noqa: BLE001
        pass

    return [], None, "unavailable"


def get_kalshi_settlement_temperature(
    event_ticker: str,
    *,
    client: KalshiClient | None = None,
    force_refresh: bool = False,
    settled_markets: list[dict[str, Any]] | None = None,
) -> SettlementObservation | None:
    """Recover settlement temperature; None on parse/agreement failure."""
    clinyc_cache_dir()
    path = observation_cache_path(event_ticker)
    if path.exists() and not force_refresh:
        cached = cache.read_json(path, default=None)
        if isinstance(cached, dict) and cached.get("event_ticker") == event_ticker:
            audit_raw = cached.get("expiration_audit")
            audit = None
            if isinstance(audit_raw, dict):
                audit = ExpirationValueAudit(**{
                    k: audit_raw[k]
                    for k in (
                        "market_count",
                        "expiration_values_seen",
                        "normalized_expiration_value",
                        "agreement",
                        "resolution_status",
                        "warnings",
                    )
                    if k in audit_raw
                })
            return SettlementObservation(
                event_ticker=str(cached.get("event_ticker")),
                target_date=cached.get("target_date"),
                settlement_temperature_f=(
                    float(cached["settlement_temperature_f"])
                    if cached.get("settlement_temperature_f") is not None
                    else None
                ),
                settlement_source_regime=str(
                    cached.get("settlement_source_regime") or REGIME_UNKNOWN
                ),
                source_url=cached.get("source_url"),
                retrieved_at=str(cached.get("retrieved_at") or ""),
                retrieval_method=str(cached.get("retrieval_method") or "cache"),
                raw_cache_path=str(path),
                parsing_confidence=str(cached.get("parsing_confidence") or "low"),
                warnings=list(cached.get("warnings") or []),
                resolution_status=str(cached.get("resolution_status") or RESOLUTION_MISSING),
                expiration_audit=audit,
            )

    client = client or KalshiClient()
    markets, event_meta, method = load_markets_for_event(
        event_ticker,
        client=client,
        settled_markets=settled_markets,
    )
    retrieved_at = datetime.now(timezone.utc).isoformat()
    target_date = get_event_date(event_ticker)
    warnings: list[str] = []

    if not markets:
        warnings.append("no markets available for settlement recovery")
        obs = SettlementObservation(
            event_ticker=event_ticker,
            target_date=target_date,
            settlement_temperature_f=None,
            settlement_source_regime=REGIME_UNKNOWN,
            source_url=None,
            retrieved_at=retrieved_at,
            retrieval_method=method,
            raw_cache_path=str(path),
            parsing_confidence="none",
            warnings=warnings,
            resolution_status=RESOLUTION_NO_MARKETS,
            expiration_audit=audit_expiration_values([]),
        )
        cache.write_json(path, obs.to_dict())
        return None

    audit = audit_expiration_values(markets)
    regime = _regime_from_markets(markets, event_meta=event_meta)
    source_url = f"https://kalshi.com/markets/{event_ticker.lower()}"

    if not audit.agreement or audit.normalized_expiration_value is None:
        obs = SettlementObservation(
            event_ticker=event_ticker,
            target_date=target_date,
            settlement_temperature_f=None,
            settlement_source_regime=regime,
            source_url=source_url,
            retrieved_at=retrieved_at,
            retrieval_method=method,
            raw_cache_path=str(path),
            parsing_confidence="rejected",
            warnings=warnings + list(audit.warnings),
            resolution_status=audit.resolution_status,
            expiration_audit=audit,
        )
        cache.write_json(path, obs.to_dict())
        return None

    obs = SettlementObservation(
        event_ticker=event_ticker,
        target_date=target_date,
        settlement_temperature_f=float(audit.normalized_expiration_value),
        settlement_source_regime=regime,
        source_url=source_url,
        retrieved_at=retrieved_at,
        retrieval_method=method,
        raw_cache_path=str(path),
        parsing_confidence="high",
        warnings=warnings + list(audit.warnings),
        resolution_status=RESOLUTION_OK,
        expiration_audit=audit,
    )
    cache.write_json(path, obs.to_dict())
    return obs
