"""Shared public Kalshi Trade API client (no credentials)."""

from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import quote

import requests

from kalshi import cache


def encode_path_segment(value: str) -> str:
    """Percent-encode a single URL path segment (including '%')."""
    return quote(str(value), safe="")


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_LIMIT = 1000
DEFAULT_MIN_DELAY_SEC = 0.25
MAX_RETRIES = 5


class KalshiAPIError(RuntimeError):
    """Raised when a Kalshi request ultimately fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str | None = None,
        api_error_code: str | None = None,
        api_error_message: str | None = None,
        failure_kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.api_error_code = api_error_code
        self.api_error_message = api_error_message
        self.failure_kind = failure_kind


class KalshiNotFoundError(KalshiAPIError):
    """Raised for permanent HTTP 404 responses (do not retry)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 404,
        path: str | None = None,
        api_error_code: str | None = None,
        api_error_message: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            path=path,
            api_error_code=api_error_code,
            api_error_message=api_error_message,
            failure_kind="http_404",
        )

class KalshiClient:
    """Public market-data client with pagination, retry, and rate delay."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        min_delay_sec: float = DEFAULT_MIN_DELAY_SEC,
        session: requests.Session | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_delay_sec = min_delay_sec
        self.session = session or requests.Session()
        self.progress = progress or (lambda _msg: None)
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_delay_sec:
            time.sleep(self.min_delay_sec - elapsed)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    timeout=self.timeout,
                )
                self._last_request_at = time.monotonic()

                # Permanent client misses: never retry.
                if response.status_code == 404:
                    raise KalshiNotFoundError(
                        f"Not found: {path}",
                        status_code=404,
                        path=path,
                        api_error_message=response.text[:200] if response.text else None,
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}",
                        response=response,
                    )

                # Other 4xx are also permanent for our purposes.
                if 400 <= response.status_code < 500:
                    raise KalshiAPIError(
                        f"HTTP {response.status_code} for {path}: {response.text[:200]}",
                        status_code=response.status_code,
                        path=path,
                        api_error_message=response.text[:200] if response.text else None,
                        failure_kind=(
                            "http_400" if response.status_code == 400 else "other"
                        ),
                    )

                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as error:
                    raise KalshiAPIError(
                        f"JSON decode error from {path}: {error}",
                        status_code=response.status_code,
                        path=path,
                        failure_kind="json_decode_error",
                    ) from error
                if not isinstance(payload, dict):
                    raise KalshiAPIError(
                        f"Malformed JSON object from {path}",
                        status_code=response.status_code,
                        path=path,
                        failure_kind="json_decode_error",
                    )
                return payload

            except KalshiNotFoundError:
                raise
            except KalshiAPIError:
                # Non-retryable application/client errors.
                raise
            except requests.Timeout as error:
                last_error = error
                if attempt + 1 >= MAX_RETRIES:
                    raise KalshiAPIError(
                        f"Timeout for {path}: {error}",
                        path=path,
                        failure_kind="timeout",
                    ) from error
                backoff = min(30.0, (2**attempt) + 0.1)
                self.progress(
                    f"Retry {attempt + 1}/{MAX_RETRIES} for {path}: {error}"
                )
                time.sleep(backoff)
            except requests.ConnectionError as error:
                last_error = error
                if attempt + 1 >= MAX_RETRIES:
                    raise KalshiAPIError(
                        f"Connection error for {path}: {error}",
                        path=path,
                        failure_kind="connection_error",
                    ) from error
                backoff = min(30.0, (2**attempt) + 0.1)
                self.progress(
                    f"Retry {attempt + 1}/{MAX_RETRIES} for {path}: {error}"
                )
                time.sleep(backoff)
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt + 1 >= MAX_RETRIES:
                    break
                backoff = min(30.0, (2**attempt) + 0.1)
                self.progress(
                    f"Retry {attempt + 1}/{MAX_RETRIES} for {path}: {error}"
                )
                time.sleep(backoff)

        status = None
        kind = "other"
        if isinstance(last_error, requests.HTTPError) and last_error.response is not None:
            status = last_error.response.status_code
            if status == 429:
                kind = "http_429"
            elif status >= 500:
                kind = "http_5xx"
        raise KalshiAPIError(
            f"Request failed for {path}: {last_error}",
            status_code=status,
            path=path,
            failure_kind=kind,
        )

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def paginate(
        self,
        path: str,
        *,
        item_key: str,
        params: dict[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        max_items: int | None = None,
        progress_label: str | None = None,
        page_callback: Callable[[int, list[dict], str | None], None] | None = None,
        start_cursor: str | None = None,
        start_page_index: int = 0,
        accumulate: bool = True,
        start_count: int = 0,
    ) -> list[dict]:
        """
        Paginate an endpoint.

        When ``accumulate`` is False (disk-backed inventory downloads), only the
        current page is held in memory and an empty list is returned.
        """
        params = dict(params or {})
        params["limit"] = limit
        cursor: str | None = start_cursor
        items: list[dict] = []
        page_index = start_page_index
        total_count = int(start_count)

        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor

            data = self.get(path, params=page_params)
            page = data.get(item_key) or []
            if not isinstance(page, list):
                raise KalshiAPIError(f"Expected list at '{item_key}' for {path}")

            next_cursor = data.get("cursor") or None
            if page_callback is not None:
                page_callback(page_index, page, next_cursor)
            page_index += 1

            total_count += len(page)
            if accumulate:
                items.extend(page)
            if progress_label:
                shown = len(items) if accumulate else total_count
                self.progress(f"{progress_label}: {shown:,} so far")

            if max_items is not None and total_count >= max_items:
                if accumulate:
                    keep = max(0, max_items - int(start_count))
                    return items[:keep]
                return []

            cursor = next_cursor
            if not cursor:
                break

        return items if accumulate else []

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def get_historical_cutoff(self) -> dict[str, Any]:
        return self.get("/historical/cutoff")

    def fetch_historical_cutoff_force_network(self) -> dict[str, Any]:
        """Always hit the network for /historical/cutoff (no cache read).

        On success the caller should atomically replace the cached cutoff file.
        On failure this raises; callers must not fall back to a stale cache.
        """
        payload = self.get_historical_cutoff()
        if not isinstance(payload, dict) or not payload.get("market_settled_ts"):
            raise KalshiAPIError(
                "Invalid /historical/cutoff response: missing market_settled_ts",
                path="/historical/cutoff",
                failure_kind="other",
            )
        return payload

    def get_historical_markets(
        self,
        *,
        series_ticker: str | None = None,
        mve_filter: str | None = "exclude",
        limit: int = DEFAULT_PAGE_LIMIT,
        max_items: int | None = None,
        extra_params: dict[str, Any] | None = None,
        page_callback: Callable[[int, list[dict], str | None], None] | None = None,
        start_cursor: str | None = None,
        start_page_index: int = 0,
        accumulate: bool | None = None,
        start_count: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = dict(extra_params or {})
        if series_ticker:
            params["series_ticker"] = series_ticker
        if mve_filter:
            params["mve_filter"] = mve_filter
        should_accumulate = (
            accumulate if accumulate is not None else page_callback is None
        )
        return self.paginate(
            "/historical/markets",
            item_key="markets",
            params=params,
            limit=limit,
            max_items=max_items,
            progress_label="Historical markets",
            page_callback=page_callback,
            start_cursor=start_cursor,
            start_page_index=start_page_index,
            accumulate=should_accumulate,
            start_count=start_count,
        )

    def get_markets(
        self,
        *,
        status: str | None = None,
        series_ticker: str | None = None,
        mve_filter: str | None = "exclude",
        min_settled_ts: Any = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        max_items: int | None = None,
        extra_params: dict[str, Any] | None = None,
        page_callback: Callable[[int, list[dict], str | None], None] | None = None,
        start_cursor: str | None = None,
        start_page_index: int = 0,
        accumulate: bool | None = None,
        start_count: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = dict(extra_params or {})
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if mve_filter:
            params["mve_filter"] = mve_filter
        if min_settled_ts is not None:
            params["min_settled_ts"] = min_settled_ts
        should_accumulate = (
            accumulate if accumulate is not None else page_callback is None
        )
        return self.paginate(
            "/markets",
            item_key="markets",
            params=params,
            limit=limit,
            max_items=max_items,
            progress_label=f"Markets status={status or 'any'}",
            page_callback=page_callback,
            start_cursor=start_cursor,
            start_page_index=start_page_index,
            accumulate=should_accumulate,
            start_count=start_count,
        )

    def get_event(
        self,
        event_ticker: str,
        *,
        with_nested_markets: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] | None = None
        if with_nested_markets:
            params = {"with_nested_markets": "true"}
        data = self.get(
            f"/events/{encode_path_segment(event_ticker)}",
            params=params,
        )
        event = data.get("event") or data
        if not isinstance(event, dict):
            return event
        # Prefer nested markets; fall back to deprecated top-level markets.
        if with_nested_markets and not event.get("markets"):
            top_markets = data.get("markets")
            if isinstance(top_markets, list):
                event = dict(event)
                event["markets"] = top_markets
        return event

    def get_series(self, series_ticker: str) -> dict[str, Any]:
        data = self.get(f"/series/{encode_path_segment(series_ticker)}")
        return data.get("series") or data

    def get_series_list(self, *, category: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        data = self.get("/series", params=params)
        return data.get("series") or []

    def get_fee_changes(
        self,
        *,
        series_ticker: str | None = None,
        show_historical: bool = True,
    ) -> list[dict]:
        params: dict[str, Any] = {"show_historical": str(show_historical).lower()}
        if series_ticker:
            params["series_ticker"] = series_ticker
        data = self.get("/series/fee_changes", params=params)
        return (
            data.get("series_fee_change_arr")
            or data.get("fee_changes")
            or data.get("series_fee_changes")
            or []
        )

    def get_event_fee_changes(
        self,
        *,
        event_ticker: str | None = None,
        show_historical: bool = True,
    ) -> list[dict]:
        """Historical event fee override/clear rows when the API exposes them."""
        params: dict[str, Any] = {"show_historical": str(show_historical).lower()}
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = self.get("/events/fee_changes", params=params)
        return (
            data.get("event_fee_change_arr")
            or data.get("fee_changes")
            or data.get("event_fee_changes")
            or []
        )

    def get_historical_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> list[dict]:
        data = self.get(
            f"/historical/markets/{encode_path_segment(ticker)}/candlesticks",
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
        return data.get("candlesticks") or []

    def get_series_market_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> list[dict]:
        """Recent/live-tier candlesticks (not /markets/{ticker}/candlesticks)."""
        data = self.get(
            f"/series/{encode_path_segment(series_ticker)}"
            f"/markets/{encode_path_segment(ticker)}/candlesticks",
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
        return data.get("candlesticks") or []

    def get_candlesticks_for_market(
        self,
        *,
        ticker: str,
        series_ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
        use_historical: bool,
    ) -> list[dict]:
        if use_historical:
            return self.get_historical_candlesticks(
                ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period_interval,
            )
        return self.get_series_market_candlesticks(
            series_ticker,
            ticker,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=period_interval,
        )


def get_open_markets(series_ticker: str, client: KalshiClient | None = None) -> list[dict]:
    """Compatibility helper used by the live weather CLI."""
    client = client or KalshiClient()
    return client.get_markets(
        status="open",
        series_ticker=series_ticker,
        mve_filter=None,
    )


def get_historical_markets_for_series(
    series_ticker: str,
    client: KalshiClient | None = None,
) -> list[dict]:
    """Compatibility helper used by the NYC weather backtest."""
    client = client or KalshiClient()
    return client.get_historical_markets(
        series_ticker=series_ticker,
        mve_filter=None,
    )


# Ensure cache dirs exist when the client package is imported in research runs.
cache.ensure_dirs()
