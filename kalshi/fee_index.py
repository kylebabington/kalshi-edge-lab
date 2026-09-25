"""Per-series fee metadata cache/index (independent of market simulation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from kalshi import cache
from kalshi.client import KalshiAPIError, KalshiClient, KalshiNotFoundError
from kalshi.fees import (
    CODE_EVENT_FEE_CHANGE_FAILURE,
    CODE_FEE_CHANGE_FETCH_FAILURE,
    CODE_SERIES_FETCH_FAILURE,
    CODE_SERIES_NOT_FOUND,
    CODE_SERIES_PARSE_FAILURE,
    CODE_UNRESOLVED_FEE_METADATA,
    FeeResolutionError,
    FeeSchedule,
    UnsupportedFeeError,
    compute_taker_fee,
    resolve_fee_schedule,
)


ProgressFn = Callable[[str], None]


@dataclass
class SeriesFeeBundle:
    series_ticker: str
    series_metadata: dict[str, Any] | None
    fee_changes: list[dict]
    fetched_at: str
    resolution_status: str
    series_http_status: int | None = None
    series_error: str | None = None
    fee_change_http_status: int | None = None
    fee_change_error: str | None = None
    fee_change_row_count: int = 0


@dataclass
class EventFeeBundle:
    event_ticker: str
    event_metadata: dict[str, Any] | None
    event_fee_changes: list[dict]
    fetched_at: str
    resolution_status: str
    event_http_status: int | None = None
    event_error: str | None = None
    fee_change_http_status: int | None = None
    fee_change_error: str | None = None


@dataclass
class FeeIndex:
    """Fetch and cache fee metadata once per series (and event when needed)."""

    client: KalshiClient
    refresh: bool = False
    progress: ProgressFn = field(default=lambda _msg: None)
    _series_memo: dict[str, SeriesFeeBundle] = field(default_factory=dict)
    _event_memo: dict[str, EventFeeBundle] = field(default_factory=dict)
    _fetch_counts: dict[str, int] = field(default_factory=dict)
    _event_fee_changes_supported: bool | None = None

    def series_fetch_count(self, series_ticker: str) -> int:
        return int(self._fetch_counts.get(f"series:{series_ticker}", 0))

    def get_series_bundle(self, series_ticker: str) -> SeriesFeeBundle:
        if not series_ticker:
            return SeriesFeeBundle(
                series_ticker="",
                series_metadata=None,
                fee_changes=[],
                fetched_at=_now_iso(),
                resolution_status=CODE_UNRESOLVED_FEE_METADATA,
                series_error="empty series_ticker",
            )
        if series_ticker in self._series_memo and not self.refresh:
            return self._series_memo[series_ticker]

        path = cache.fee_series_path(series_ticker)
        if path.exists() and not self.refresh:
            payload = cache.read_json(path, default=None)
            if isinstance(payload, dict) and payload.get("series_ticker"):
                bundle = _bundle_from_payload(payload)
                self._series_memo[series_ticker] = bundle
                return bundle

        bundle = self._fetch_series_bundle(series_ticker)
        self._series_memo[series_ticker] = bundle
        cache.ensure_dirs()
        cache.write_json(path, _bundle_to_payload(bundle))
        return bundle

    def get_event_bundle(self, event_ticker: str) -> EventFeeBundle:
        if not event_ticker:
            return EventFeeBundle(
                event_ticker="",
                event_metadata=None,
                event_fee_changes=[],
                fetched_at=_now_iso(),
                resolution_status="ok",
            )
        if event_ticker in self._event_memo and not self.refresh:
            return self._event_memo[event_ticker]

        path = cache.event_path(event_ticker)
        event_meta: dict[str, Any] | None = None
        event_status: int | None = None
        event_error: str | None = None
        if path.exists() and not self.refresh:
            cached = cache.read_json(path, default=None)
            if isinstance(cached, dict) and not cached.get("_missing"):
                event_meta = cached.get("event") if "event" in cached else cached

        if event_meta is None:
            try:
                event_meta = self.client.get_event(event_ticker)
                event_status = 200
                cache.write_json(path, event_meta)
            except KalshiNotFoundError as error:
                event_status = 404
                event_error = str(error)
                cache.write_json(path, {"_missing": True, "error": str(error)})
            except KalshiAPIError as error:
                event_status = error.status_code
                event_error = str(error)
                if path.exists():
                    cached = cache.read_json(path, default=None)
                    if isinstance(cached, dict) and not cached.get("_missing"):
                        event_meta = cached.get("event") if "event" in cached else cached

        fee_changes: list[dict] = []
        fee_status: int | None = None
        fee_error: str | None = None
        if self._event_fee_changes_supported is False:
            fee_status = 404
            fee_changes = []
        else:
            try:
                fee_changes = self.client.get_event_fee_changes(
                    event_ticker=event_ticker,
                    show_historical=True,
                )
                fee_status = 200
                self._event_fee_changes_supported = True
            except KalshiNotFoundError:
                # Endpoint or event may not expose historical overrides.
                fee_status = 404
                fee_changes = []
                # First 404 on the collection endpoint → skip further probes.
                if self._event_fee_changes_supported is None:
                    self._event_fee_changes_supported = False
            except KalshiAPIError as error:
                fee_status = error.status_code
                fee_error = str(error)
                fee_changes = []
                if error.status_code == 404 and self._event_fee_changes_supported is None:
                    self._event_fee_changes_supported = False

        # Embed any fee-change array already present on the event payload.
        if not fee_changes and isinstance(event_meta, dict):
            embedded = (
                event_meta.get("event_fee_change_arr")
                or event_meta.get("fee_changes")
                or event_meta.get("event_fee_changes")
                or []
            )
            if isinstance(embedded, list):
                fee_changes = embedded

        status = "ok"
        if event_error and event_meta is None:
            status = CODE_EVENT_FEE_CHANGE_FAILURE
        elif fee_error and fee_status not in (None, 404):
            status = CODE_EVENT_FEE_CHANGE_FAILURE

        bundle = EventFeeBundle(
            event_ticker=event_ticker,
            event_metadata=event_meta if isinstance(event_meta, dict) else None,
            event_fee_changes=list(fee_changes),
            fetched_at=_now_iso(),
            resolution_status=status,
            event_http_status=event_status,
            event_error=event_error,
            fee_change_http_status=fee_status,
            fee_change_error=fee_error,
        )
        self._event_memo[event_ticker] = bundle
        return bundle

    def resolve_for_market(
        self,
        *,
        series_ticker: str,
        entry_ts: str | datetime_like,
        event_ticker: str | None = None,
        event: dict | None = None,
    ) -> FeeSchedule:
        """Resolve fee schedule using per-series cache (+ optional event)."""
        series_bundle = self.get_series_bundle(series_ticker)
        if series_bundle.resolution_status == CODE_FEE_CHANGE_FETCH_FAILURE and not (
            series_bundle.fee_changes or series_bundle.series_metadata
        ):
            raise FeeResolutionError(
                series_bundle.fee_change_error
                or f"Fee-change fetch failure for {series_ticker}",
                code=CODE_FEE_CHANGE_FETCH_FAILURE,
            )
        if (
            series_bundle.resolution_status == CODE_SERIES_NOT_FOUND
            and not series_bundle.fee_changes
        ):
            raise FeeResolutionError(
                series_bundle.series_error
                or f"Series metadata not found for {series_ticker}",
                code=CODE_SERIES_NOT_FOUND,
            )
        if (
            series_bundle.resolution_status == CODE_SERIES_FETCH_FAILURE
            and not series_bundle.fee_changes
            and not series_bundle.series_metadata
        ):
            raise FeeResolutionError(
                series_bundle.series_error
                or f"Series metadata fetch failure for {series_ticker}",
                code=CODE_SERIES_FETCH_FAILURE,
            )

        event_meta = event
        event_fee_changes: list[dict] | None = None
        if event_ticker:
            event_bundle = self.get_event_bundle(event_ticker)
            if event_meta is None:
                event_meta = event_bundle.event_metadata
            event_fee_changes = event_bundle.event_fee_changes
            if (
                event_bundle.resolution_status == CODE_EVENT_FEE_CHANGE_FAILURE
                and event_bundle.fee_change_error
                and event_bundle.fee_change_http_status
                not in (None, 404)
            ):
                # Only fail hard when fee-change history was required and broken.
                # Snapshot overrides on event_meta can still apply.
                pass

        try:
            return resolve_fee_schedule(
                series_ticker=series_ticker,
                entry_ts=entry_ts,
                fee_changes=series_bundle.fee_changes,
                event=event_meta or {},
                series=series_bundle.series_metadata or {},
                event_fee_changes=event_fee_changes,
            )
        except FeeResolutionError:
            raise
        except (TypeError, ValueError, KeyError) as error:
            raise FeeResolutionError(
                f"Series metadata parse failure for {series_ticker}: {error}",
                code=CODE_SERIES_PARSE_FAILURE,
            ) from error

    def _fetch_series_bundle(self, series_ticker: str) -> SeriesFeeBundle:
        self._fetch_counts[f"series:{series_ticker}"] = (
            self._fetch_counts.get(f"series:{series_ticker}", 0) + 1
        )
        self.progress(f"Fee index fetch series={series_ticker}")

        series_meta: dict[str, Any] | None = None
        series_status: int | None = None
        series_error: str | None = None

        # Prefer existing series cache when not refreshing.
        series_path = cache.series_path(series_ticker)
        if series_path.exists() and not self.refresh:
            cached = cache.read_json(series_path, default=None)
            if isinstance(cached, dict) and not cached.get("_missing"):
                series_meta = cached.get("series") if "series" in cached else cached
                series_status = 200

        if series_meta is None:
            try:
                series_meta = self.client.get_series(series_ticker)
                series_status = 200
                cache.write_json(series_path, series_meta)
            except KalshiNotFoundError as error:
                series_status = 404
                series_error = str(error)
                cache.write_json(series_path, {"_missing": True, "error": str(error)})
            except KalshiAPIError as error:
                series_status = error.status_code
                series_error = str(error)
                if series_path.exists():
                    cached = cache.read_json(series_path, default=None)
                    if isinstance(cached, dict) and not cached.get("_missing"):
                        series_meta = (
                            cached.get("series") if "series" in cached else cached
                        )

        fee_changes: list[dict] = []
        fee_status: int | None = None
        fee_error: str | None = None
        try:
            fee_changes = self.client.get_fee_changes(
                series_ticker=series_ticker,
                show_historical=True,
            )
            fee_status = 200
        except KalshiAPIError as error:
            fee_status = error.status_code
            fee_error = str(error)
            # Seed from global fee_changes cache when per-series fetch fails.
            global_path = cache.fee_changes_path()
            if global_path.exists():
                payload = cache.read_json(
                    global_path, default={"series_fee_change_arr": []}
                )
                all_changes = payload.get("series_fee_change_arr") or []
                fee_changes = [
                    c
                    for c in all_changes
                    if isinstance(c, dict) and c.get("series_ticker") == series_ticker
                ]

        if series_status == 404 and series_meta is None:
            status = CODE_SERIES_NOT_FOUND
        elif series_error and series_meta is None:
            status = CODE_SERIES_FETCH_FAILURE
        elif fee_error and not fee_changes and series_meta is None:
            status = CODE_FEE_CHANGE_FETCH_FAILURE
        elif fee_error and not fee_changes:
            status = CODE_FEE_CHANGE_FETCH_FAILURE
        else:
            status = "ok"

        return SeriesFeeBundle(
            series_ticker=series_ticker,
            series_metadata=series_meta if isinstance(series_meta, dict) else None,
            fee_changes=list(fee_changes),
            fetched_at=_now_iso(),
            resolution_status=status,
            series_http_status=series_status,
            series_error=series_error,
            fee_change_http_status=fee_status,
            fee_change_error=fee_error,
            fee_change_row_count=len(fee_changes),
        )


# Avoid importing datetime in the public signature only for typing clarity.
datetime_like = Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _bundle_to_payload(bundle: SeriesFeeBundle) -> dict[str, Any]:
    return {
        "series_ticker": bundle.series_ticker,
        "series_metadata": bundle.series_metadata,
        "fee_changes": bundle.fee_changes,
        "fetched_at": bundle.fetched_at,
        "resolution_status": bundle.resolution_status,
        "series_http_status": bundle.series_http_status,
        "series_error": bundle.series_error,
        "fee_change_http_status": bundle.fee_change_http_status,
        "fee_change_error": bundle.fee_change_error,
        "fee_change_row_count": bundle.fee_change_row_count,
    }


def _bundle_from_payload(payload: dict[str, Any]) -> SeriesFeeBundle:
    changes = payload.get("fee_changes") or []
    if not isinstance(changes, list):
        changes = []
    return SeriesFeeBundle(
        series_ticker=str(payload.get("series_ticker") or ""),
        series_metadata=payload.get("series_metadata")
        if isinstance(payload.get("series_metadata"), dict)
        else None,
        fee_changes=changes,
        fetched_at=str(payload.get("fetched_at") or ""),
        resolution_status=str(payload.get("resolution_status") or "ok"),
        series_http_status=payload.get("series_http_status"),
        series_error=payload.get("series_error"),
        fee_change_http_status=payload.get("fee_change_http_status"),
        fee_change_error=payload.get("fee_change_error"),
        fee_change_row_count=int(
            payload.get("fee_change_row_count")
            if payload.get("fee_change_row_count") is not None
            else len(changes)
        ),
    )


def classify_series_metadata_failure_cause(
    *,
    series_ticker: str,
    market_row_had_series: bool,
    bundle: SeriesFeeBundle | None,
) -> str:
    """Exact cause label for formerly generic series_metadata_failure rows."""
    if not market_row_had_series and bundle is not None:
        meta = bundle.series_metadata or {}
        if meta.get("fee_type") is not None and meta.get("fee_multiplier") is not None:
            return "lean_row_missing_series_payload_cache_ok"
        if bundle.series_http_status == 404 or bundle.resolution_status == CODE_SERIES_NOT_FOUND:
            return "HTTP 404"
        if bundle.series_http_status == 400:
            return "HTTP 400"
        if bundle.series_http_status == 429:
            return "HTTP 429"
        if bundle.series_error and "timeout" in str(bundle.series_error).lower():
            return "timeout"
        if bundle.series_metadata is None and bundle.series_error:
            return "series_metadata_fetch_failure"
        if bundle.series_metadata is not None:
            if meta.get("fee_type") is None:
                return "API response missing fee_type"
            if meta.get("fee_multiplier") is None:
                return "API response missing fee_multiplier"
        if bundle.fee_change_error and not bundle.fee_changes:
            return "fee_change_fetch_failure"
        return "missing cached metadata"

    if bundle is None:
        return "missing cached metadata"

    meta = bundle.series_metadata or {}
    if bundle.series_http_status == 404 or bundle.resolution_status == CODE_SERIES_NOT_FOUND:
        return "HTTP 404"
    if bundle.series_http_status == 400:
        return "HTTP 400"
    if bundle.series_http_status == 429:
        return "HTTP 429"
    if bundle.series_error and "timeout" in str(bundle.series_error).lower():
        return "timeout"
    if bundle.series_metadata is None and bundle.series_error:
        return "series_metadata_fetch_failure"
    if bundle.series_metadata is not None:
        if meta.get("fee_type") is None:
            return "API response missing fee_type"
        if meta.get("fee_multiplier") is None:
            return "API response missing fee_multiplier"
    if bundle.resolution_status == CODE_SERIES_PARSE_FAILURE:
        return "parser error"
    return "other"


__all__ = [
    "EventFeeBundle",
    "FeeIndex",
    "SeriesFeeBundle",
    "classify_series_metadata_failure_cause",
    "compute_taker_fee",
    "UnsupportedFeeError",
    "FeeResolutionError",
]
