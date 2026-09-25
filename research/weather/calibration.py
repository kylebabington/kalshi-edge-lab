"""Historical GFS residual calibration dataset and error metrics.

Residual convention:
    residual_f = actual_high_f - forecast_high_f

Predictive sampling:
    possible_actual = current_forecast + historical_residual
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from historical_weather import get_gfs_run_high
from kalshi import cache
from nws import get_historical_knyc_highs
from research.weather.models import (
    ACTUAL_SOURCE_IEM_NWS_CLI,
    ACTUAL_SOURCE_KALSHI_EXPIRATION,
    GFS_PUBLICATION_LATENCY,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    RESIDUAL_CONVENTION,
    SERIES_TICKER,
    STANDARDIZED_RUNS,
)
from research.weather.resolution import (
    build_weather_resolution,
    get_event_date,
    get_outcome_label,
    get_winning_market,
    group_markets_by_event,
    is_range_bucket_event,
    season_for_month,
)


CALIBRATION_DIR = cache.REPO_ROOT / "data" / "weather" / "calibration"
CALIBRATION_CSV = CALIBRATION_DIR / "gfs_errors.csv"

# Open-Meteo Single Runs archive gate (existing backtest convention).
MIN_SINGLE_RUN_DATE = "2026-04-02"

CSV_FIELDS = [
    "event_ticker",
    "target_date",
    "model",
    "run_id",
    "run_label",
    "model_run_time_utc",
    "model_available_as_of",
    "forecast_high_f",
    "actual_high_f",
    "actual_source",
    "target_regime",
    "residual_f",
    "forecast_error_f",
    "month",
    "season",
    "lead_hours",
    "winning_market_ticker",
    "winning_bucket_label",
    "settlement_source_regime",
    "resolution_certainty",
    "source",
    "provenance",
]


def ensure_weather_cache_dirs() -> None:
    cache.ensure_dirs()
    for sub in (
        "weather",
        "weather/gfs",
        "weather/gefs",
        "weather/observations",
        "weather/resolution",
        "weather/calibration",
    ):
        (cache.CACHE_ROOT / sub).mkdir(parents=True, exist_ok=True)
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    (cache.REPO_ROOT / "data" / "weather").mkdir(parents=True, exist_ok=True)


def model_run_init_utc(target_date: str, run_date_offset: int, run_hour: int) -> datetime:
    target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    run_day = target + timedelta(days=run_date_offset)
    return run_day.replace(hour=run_hour, minute=0, second=0, microsecond=0)


def model_available_as_of(run_init: datetime) -> datetime:
    return run_init + GFS_PUBLICATION_LATENCY


def is_forecast_available(*, run_init: datetime, as_of: datetime) -> bool:
    """No-lookahead gate: model may only be used if available by as_of."""
    return model_available_as_of(run_init) <= as_of


def compute_residual(*, actual_high_f: float, forecast_high_f: float) -> float:
    """residual_f = actual - forecast (predictive sampling convention)."""
    return float(actual_high_f) - float(forecast_high_f)


def lead_hours(target_date: str, run_init: datetime) -> float:
    """Hours from model init to local-midnight start of target NY calendar day (approx UTC)."""
    target_start = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (target_start - run_init).total_seconds() / 3600.0


@dataclass
class CalibrationRow:
    event_ticker: str
    target_date: str
    model: str
    run_id: str
    run_label: str
    model_run_time_utc: str
    model_available_as_of: str
    forecast_high_f: float | None
    actual_high_f: float | None
    residual_f: float | None
    month: int | None
    season: str | None
    lead_hours: float | None
    winning_market_ticker: str | None
    winning_bucket_label: str | None
    settlement_source_regime: str
    resolution_certainty: str
    source: str
    provenance: str
    actual_source: str = "iem_nws_cli"
    target_regime: str = REGIME_NWS_CLI_KNYC

    @property
    def forecast_error_f(self) -> float | None:
        # Alias: same as residual_f under the new convention.
        return self.residual_f

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "event_ticker": self.event_ticker,
            "target_date": self.target_date,
            "model": self.model,
            "run_id": self.run_id,
            "run_label": self.run_label,
            "model_run_time_utc": self.model_run_time_utc,
            "model_available_as_of": self.model_available_as_of,
            "forecast_high_f": self.forecast_high_f,
            "actual_high_f": self.actual_high_f,
            "actual_source": self.actual_source,
            "target_regime": self.target_regime,
            "residual_f": self.residual_f,
            "forecast_error_f": self.forecast_error_f,
            "month": self.month,
            "season": self.season,
            "lead_hours": self.lead_hours,
            "winning_market_ticker": self.winning_market_ticker,
            "winning_bucket_label": self.winning_bucket_label,
            "settlement_source_regime": self.settlement_source_regime,
            "resolution_certainty": self.resolution_certainty,
            "source": self.source,
            "provenance": self.provenance,
        }


def percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def error_metrics(residuals: list[float]) -> dict[str, Any]:
    """Summarize residual_f = actual - forecast."""
    if not residuals:
        return {
            "N": 0,
            "mean_error": None,
            "median_error": None,
            "MAE": None,
            "RMSE": None,
            "bias": None,
            "std": None,
            "p05": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
        }

    sorted_r = sorted(residuals)
    mean_err = statistics.fmean(residuals)
    abs_err = [abs(r) for r in residuals]
    sq = [r * r for r in residuals]
    return {
        "N": len(residuals),
        "mean_error": mean_err,
        "median_error": statistics.median(residuals),
        "MAE": statistics.fmean(abs_err),
        "RMSE": math.sqrt(statistics.fmean(sq)),
        "bias": mean_err,  # under residual = actual - forecast: positive ⇒ model too cold
        "std": statistics.pstdev(residuals) if len(residuals) > 1 else 0.0,
        "p05": percentile(sorted_r, 5),
        "p10": percentile(sorted_r, 10),
        "p25": percentile(sorted_r, 25),
        "p50": percentile(sorted_r, 50),
        "p75": percentile(sorted_r, 75),
        "p90": percentile(sorted_r, 90),
        "p95": percentile(sorted_r, 95),
    }


def select_usable_events(markets: list[dict]) -> list[dict[str, Any]]:
    grouped = group_markets_by_event(markets)
    usable: list[dict[str, Any]] = []
    for event_ticker, event_markets in grouped.items():
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if target_date is None or target_date < MIN_SINGLE_RUN_DATE:
            continue
        winner = get_winning_market(event_markets)
        if winner is None:
            continue
        resolution = build_weather_resolution(
            event_ticker=event_ticker,
            markets=event_markets,
        )
        usable.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "markets": event_markets,
                "winner": winner,
                "resolution": resolution,
            }
        )
    usable.sort(key=lambda e: e["target_date"])
    return usable


def build_calibration_rows(
    markets: list[dict],
    *,
    progress: Callable[[str], None] | None = None,
    forecast_fetcher: Callable[..., float | None] | None = None,
) -> list[CalibrationRow]:
    """Build auditable calibration rows for all standardized runs."""
    ensure_weather_cache_dirs()
    fetch = forecast_fetcher or get_gfs_run_high
    log = progress or (lambda _m: None)

    usable = select_usable_events(markets)
    log(f"Usable range-bucket events (>= {MIN_SINGLE_RUN_DATE}): {len(usable)}")

    years = sorted({int(e["target_date"][:4]) for e in usable})
    highs: dict[str, float] = {}
    for year in years:
        highs.update(get_historical_knyc_highs(year))

    # Optional CLINYC / Kalshi settlement temperatures (expiration_value).
    clinyc_actuals: dict[str, float] = {}
    try:
        from research.weather.clinyc import (
            fetch_settled_kxhighny_markets,
            get_kalshi_settlement_temperature,
        )

        settled = fetch_settled_kxhighny_markets()
        for event in usable:
            if event["resolution"].settlement_source_regime != REGIME_WEATHER_COMPANY_CLINYC:
                continue
            obs = get_kalshi_settlement_temperature(
                event["event_ticker"],
                settled_markets=settled,
            )
            if obs is not None and obs.settlement_temperature_f is not None:
                clinyc_actuals[event["target_date"]] = float(obs.settlement_temperature_f)
    except Exception:  # noqa: BLE001
        clinyc_actuals = {}

    retrieved_at = datetime.now(timezone.utc).isoformat()
    rows: list[CalibrationRow] = []

    for event in usable:
        target_date = event["target_date"]
        event_ticker = event["event_ticker"]
        winner = event["winner"]
        resolution = event["resolution"]
        regime = resolution.settlement_source_regime
        # Never mix residual pools across regimes. Never fill a missing CLINYC
        # actual from KNYC (or vice versa) while labeling the other source.
        if regime == REGIME_WEATHER_COMPANY_CLINYC:
            if target_date not in clinyc_actuals:
                # Skip residual emission — do not fall back to KNYC.
                continue
            actual = clinyc_actuals[target_date]
            actual_source = ACTUAL_SOURCE_KALSHI_EXPIRATION
            source_label = "open-meteo:ncep_gfs_global+kalshi:expiration_value"
            target_regime = REGIME_WEATHER_COMPANY_CLINYC
        elif regime == REGIME_NWS_CLI_KNYC:
            actual = highs.get(target_date)
            if actual is None:
                continue
            actual_source = ACTUAL_SOURCE_IEM_NWS_CLI
            source_label = "open-meteo:ncep_gfs_global+iem_nws_cli"
            target_regime = REGIME_NWS_CLI_KNYC
        else:
            # unknown / conflicting — do not emit calibration residuals
            continue
        month = int(target_date[5:7])
        season = season_for_month(month)

        for run in STANDARDIZED_RUNS:
            run_init = model_run_init_utc(
                target_date, int(run["run_date_offset"]), int(run["run_hour"])
            )
            available = model_available_as_of(run_init)
            forecast = fetch(
                target_date,
                int(run["run_date_offset"]),
                int(run["run_hour"]),
            )
            residual = None
            if forecast is not None and actual is not None:
                residual = compute_residual(
                    actual_high_f=actual,
                    forecast_high_f=forecast,
                )

            provenance = (
                f"source=open-meteo-single-runs;model=ncep_gfs_global;"
                f"run={run_init.strftime('%Y-%m-%dT%H:%M')};"
                f"available={available.isoformat()};"
                f"latency_hours={GFS_PUBLICATION_LATENCY.total_seconds() / 3600:.0f};"
                f"actual_source={actual_source};"
                f"target_regime={target_regime};"
                f"station={'CLINYC' if actual_source == ACTUAL_SOURCE_KALSHI_EXPIRATION else 'KNYC'};"
                f"retrieved_at={retrieved_at};"
                f"residual_convention={RESIDUAL_CONVENTION}"
            )

            rows.append(
                CalibrationRow(
                    event_ticker=event_ticker,
                    target_date=target_date,
                    model="ncep_gfs_global",
                    run_id=str(run["run_id"]),
                    run_label=str(run["label"]),
                    model_run_time_utc=run_init.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    model_available_as_of=available.isoformat(),
                    forecast_high_f=forecast,
                    actual_high_f=actual,
                    residual_f=residual,
                    month=month,
                    season=season,
                    lead_hours=lead_hours(target_date, run_init),
                    winning_market_ticker=winner.get("ticker"),
                    winning_bucket_label=get_outcome_label(winner),
                    settlement_source_regime=regime,
                    resolution_certainty=resolution.resolution_certainty,
                    source=source_label,
                    provenance=provenance,
                    actual_source=actual_source,
                    target_regime=target_regime,
                )
            )

        log(f"Calibration rows for {event_ticker} ({target_date})")

    return rows


def write_calibration_csv(rows: list[CalibrationRow], path: Path | None = None) -> Path:
    ensure_weather_cache_dirs()
    out = path or CALIBRATION_CSV
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    # Build CSV text then atomic write.
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_dict())
    cache.atomic_write_text(out, buf.getvalue())
    return out


def load_calibration_csv(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or CALIBRATION_CSV
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nws_cli_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calibration must not mix TWC regime outcomes into NWS residual pools."""
    return [
        r
        for r in rows
        if (r.get("settlement_source_regime") or REGIME_NWS_CLI_KNYC) == REGIME_NWS_CLI_KNYC
        and parse_float(r.get("residual_f")) is not None
    ]


def clinyc_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CLINYC residual pool — never mixed with NWS CLI residuals."""
    from research.weather.models import REGIME_WEATHER_COMPANY_CLINYC

    return [
        r
        for r in rows
        if (r.get("settlement_source_regime") or "") == REGIME_WEATHER_COMPANY_CLINYC
        and parse_float(r.get("residual_f")) is not None
    ]


def annotate_calibration_sources(
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Ensure every calibration row has target_regime + actual_source.

    Does not refetch GFS. Does not mix residual pools. Writes CSV if rows loaded
    from disk and columns were missing/updated.
    """
    from research.weather.models import (
        ACTUAL_SOURCE_IEM_NWS_CLI,
        ACTUAL_SOURCE_KALSHI_EXPIRATION,
    )

    loaded = rows is None
    data = list(rows) if rows is not None else load_calibration_csv()
    if not data:
        return []
    changed = False
    out: list[dict[str, Any]] = []
    for row in data:
        r = dict(row)
        regime = str(
            r.get("target_regime")
            or r.get("settlement_source_regime")
            or REGIME_NWS_CLI_KNYC
        )
        if r.get("target_regime") != regime:
            r["target_regime"] = regime
            changed = True
        if not r.get("actual_source"):
            if regime == REGIME_WEATHER_COMPANY_CLINYC:
                r["actual_source"] = ACTUAL_SOURCE_KALSHI_EXPIRATION
            else:
                r["actual_source"] = ACTUAL_SOURCE_IEM_NWS_CLI
            changed = True
        elif r.get("actual_source") in {"iem_cli:KNYC", "iem_cli:knyc"}:
            r["actual_source"] = ACTUAL_SOURCE_IEM_NWS_CLI
            changed = True
        # Guard: never keep a CLINYC-labeled row that used KNYC actual.
        if (
            regime == REGIME_WEATHER_COMPANY_CLINYC
            and str(r.get("actual_source") or "").startswith("iem")
        ):
            # Drop residual rather than alias sources.
            r["residual_f"] = ""
            r["actual_high_f"] = ""
            r["actual_source"] = ACTUAL_SOURCE_KALSHI_EXPIRATION
            changed = True
        out.append(r)
    if loaded and changed:
        # Rewrite with extended fieldnames.
        ensure_weather_cache_dirs()
        import io

        buf = io.StringIO()
        fieldnames = list(CSV_FIELDS)
        for key in out[0].keys():
            if key not in fieldnames:
                fieldnames.append(key)
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in out:
            writer.writerow(row)
        cache.atomic_write_text(CALIBRATION_CSV, buf.getvalue())
    return out


def metrics_by_run(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for run in STANDARDIZED_RUNS:
        run_id = str(run["run_id"])
        residuals = [
            parse_float(r["residual_f"])
            for r in rows
            if r.get("run_id") == run_id and parse_float(r.get("residual_f")) is not None
        ]
        # type narrowing
        clean = [float(x) for x in residuals if x is not None]
        out[run_id] = {
            "label": run["label"],
            **error_metrics(clean),
        }
    return out
