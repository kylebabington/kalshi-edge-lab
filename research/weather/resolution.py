"""KXHIGHNY resolution metadata and temperature bucket parsing.

Settlement sources are time-varying:
  - Historical (through ~2026-07): NWS Climatological Report (Daily), Central Park
  - Current open markets: The Weather Company, CLINYC

Do not silently treat these as interchangeable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from research.weather.models import (
    BUCKET_BOUNDARY_ASSUMPTION,
    REGIME_CONFLICTING,
    REGIME_NWS_CLI_KNYC,
    REGIME_UNKNOWN,
    REGIME_WEATHER_COMPANY_CLINYC,
    SERIES_TICKER,
)


@dataclass(frozen=True)
class TemperatureBucket:
    """Discrete integer temperature bucket from Kalshi contract rules."""

    label: str
    lower_bound: int | None
    upper_bound: int | None
    lower_inclusive: bool
    upper_inclusive: bool
    ticker: str | None = None
    bucket_boundary_assumption: str = BUCKET_BOUNDARY_ASSUMPTION

    def contains(self, temperature: float | int) -> bool:
        """True if an integer (or near-integer) high falls in this bucket."""
        # Settlement CLI highs are whole degrees; round continuous samples.
        value = int(round(float(temperature)))

        if self.lower_bound is not None:
            if self.lower_inclusive:
                if value < self.lower_bound:
                    return False
            elif value <= self.lower_bound:
                return False

        if self.upper_bound is not None:
            if self.upper_inclusive:
                if value > self.upper_bound:
                    return False
            elif value >= self.upper_bound:
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeatherResolution:
    """Auditable settlement resolution metadata for a KXHIGHNY event."""

    series_ticker: str
    event_ticker: str
    target_date: str

    station_id: str
    timezone: str

    measurement: str
    unit: str

    market_structure: str
    contracts: list[dict[str, Any]]
    resolution_source: str

    settlement_source_regime: str
    resolution_certainty: str

    warnings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_event_date(event_ticker: str) -> str | None:
    """Convert KXHIGHNY-26SEP12 → 2026-09-12."""
    try:
        date_code = event_ticker.rsplit("-", 1)[-1]
        parsed = datetime.strptime(date_code, "%y%b%d")
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def group_markets_by_event(markets: list[dict]) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        event_ticker = market.get("event_ticker")
        if not event_ticker:
            continue
        events[str(event_ticker)].append(market)
    return dict(events)


def get_outcome_label(market: dict) -> str:
    return str(
        market.get("yes_sub_title")
        or market.get("title")
        or ""
    )


def is_range_bucket_event(markets: list[dict]) -> bool:
    """Modern mutually exclusive range-bucket structure."""
    if len(markets) < 3:
        return False

    labels = [get_outcome_label(m).lower() for m in markets]
    below_count = sum("or below" in label for label in labels)
    above_count = sum("or above" in label for label in labels)
    range_count = sum(" to " in label for label in labels)
    expected_total = below_count + above_count + range_count
    return (
        below_count == 1
        and above_count == 1
        and range_count >= 1
        and expected_total == len(markets)
    )


def get_winning_market(markets: list[dict]) -> dict | None:
    winners = [m for m in markets if m.get("result") == "yes"]
    if len(winners) != 1:
        return None
    return winners[0]


def _extract_degrees(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"-?\d+", text)]


def parse_temperature_bucket(
    market: dict,
    *,
    boundary_assumption: str = BUCKET_BOUNDARY_ASSUMPTION,
) -> TemperatureBucket | None:
    """Parse a Kalshi contract into a discrete integer TemperatureBucket.

    Prefer rules_primary / strike_type semantics when available:

      "79° or below" ↔ high < 80  →  ≤ 79 for integers
      "70° or above" ↔ high > 69  →  ≥ 70 for integers
      "86° to 87°"   ↔ between 86-87 inclusive
    """
    label = get_outcome_label(market)
    if not label:
        return None

    label_lower = label.lower()
    rules = str(market.get("rules_primary") or "")
    rules_lower = rules.lower()
    strike_type = str(market.get("strike_type") or "").lower()
    numbers = _extract_degrees(label)
    ticker = market.get("ticker")

    # Prefer explicit comparison language from rules.
    if "less than" in rules_lower or strike_type == "less":
        rule_nums = _extract_degrees(rules) or numbers
        if rule_nums:
            # "less than 80" → upper exclusive at 80 → inclusive ≤ 79
            exclusive_upper = rule_nums[-1]
            return TemperatureBucket(
                label=label,
                lower_bound=None,
                upper_bound=exclusive_upper - 1,
                lower_inclusive=True,
                upper_inclusive=True,
                ticker=ticker,
                bucket_boundary_assumption=boundary_assumption,
            )

    if "greater than" in rules_lower or strike_type in {"greater", "greater_or_equal"}:
        rule_nums = _extract_degrees(rules) or numbers
        if rule_nums:
            # "greater than 69" → ≥ 70
            exclusive_lower = rule_nums[-1]
            return TemperatureBucket(
                label=label,
                lower_bound=exclusive_lower + 1,
                upper_bound=None,
                lower_inclusive=True,
                upper_inclusive=True,
                ticker=ticker,
                bucket_boundary_assumption=boundary_assumption,
            )

    if "or below" in label_lower and numbers:
        # Label "79° or below" means ≤ 79 (integer).
        return TemperatureBucket(
            label=label,
            lower_bound=None,
            upper_bound=numbers[0],
            lower_inclusive=True,
            upper_inclusive=True,
            ticker=ticker,
            bucket_boundary_assumption=boundary_assumption,
        )

    if "or above" in label_lower and numbers:
        return TemperatureBucket(
            label=label,
            lower_bound=numbers[0],
            upper_bound=None,
            lower_inclusive=True,
            upper_inclusive=True,
            ticker=ticker,
            bucket_boundary_assumption=boundary_assumption,
        )

    if (" to " in label_lower or "between" in rules_lower or strike_type == "between") and len(
        numbers
    ) >= 2:
        lower, upper = numbers[0], numbers[1]
        return TemperatureBucket(
            label=label,
            lower_bound=lower,
            upper_bound=upper,
            lower_inclusive=True,
            upper_inclusive=True,
            ticker=ticker,
            bucket_boundary_assumption=boundary_assumption,
        )

    return None


def temperature_matches_bucket(temperature: float, bucket: TemperatureBucket) -> bool:
    return bucket.contains(temperature)


def temperature_matches_outcome(
    temperature: float,
    outcome: str,
    *,
    market: dict | None = None,
) -> bool:
    """Match temperature to an outcome label using discrete integer rules.

    Prefer passing a full market dict so rules_primary can be used.
    """
    payload = market or {"yes_sub_title": outcome}
    if market is None and "yes_sub_title" not in payload:
        payload = {"yes_sub_title": outcome}
    bucket = parse_temperature_bucket(payload)
    if bucket is None:
        return False
    return bucket.contains(temperature)


def legacy_half_degree_matches_outcome(temperature: float, outcome: str) -> bool:
    """Legacy ±0.5°F continuous mapping (modeling assumption only).

    Preserved for raw point-forecast baseline comparison. Not used for
    calibrated residual probabilities.
    """
    numbers = _extract_degrees(outcome)
    outcome_lower = outcome.lower()

    if "or below" in outcome_lower and numbers:
        return temperature < (numbers[0] + 0.5)
    if "or above" in outcome_lower and numbers:
        return temperature >= (numbers[0] - 0.5)
    if "to" in outcome_lower and len(numbers) >= 2:
        lower, upper = numbers[0], numbers[1]
        return temperature >= lower - 0.5 and temperature < upper + 0.5
    return False


def detect_settlement_source_regime(
    *,
    rules_primary: str | None = None,
    settlement_sources: list[dict] | None = None,
    series_settlement_sources: list[dict] | None = None,
) -> tuple[str, str, list[str]]:
    """Return (regime, certainty, evidence).

    Classification is rules/metadata driven — never a hardcoded calendar date.
    """
    evidence: list[str] = []
    rules = rules_primary or ""
    rules_lower = rules.lower()

    sources = list(settlement_sources or []) + list(series_settlement_sources or [])
    source_names = " ".join(
        str(s.get("name") or "") for s in sources if isinstance(s, dict)
    ).lower()

    twc_hit = (
        "weather company" in rules_lower
        or "clinyc" in rules_lower
        or "weather company" in source_names
        or "clinyc" in source_names
    )
    nws_hit = (
        "climatological report" in rules_lower
        or "national weather service" in rules_lower
        or ("central park" in rules_lower and "weather company" not in rules_lower)
        or "national weather service" in source_names
    )

    if twc_hit and nws_hit:
        evidence.append(
            "conflicting settlement signals: both NWS CLI / KNYC and "
            "Weather Company / CLINYC referenced"
        )
        return REGIME_CONFLICTING, "conflicting", evidence

    if twc_hit:
        evidence.append("rules/settlement_sources reference The Weather Company / CLINYC")
        return REGIME_WEATHER_COMPANY_CLINYC, "verified_weather_company", evidence

    if nws_hit:
        evidence.append(
            "rules_primary cites NWS Climatological Report (Daily) / Central Park"
        )
        return REGIME_NWS_CLI_KNYC, "verified_nws_cli", evidence

    if source_names:
        evidence.append(f"unclassified settlement_sources: {source_names!r}")
    if rules:
        evidence.append(f"unclassified rules_primary snippet: {rules[:160]!r}")
    return REGIME_UNKNOWN, "assumed", evidence


def build_weather_resolution(
    *,
    event_ticker: str,
    markets: list[dict],
    series_meta: dict | None = None,
    event_meta: dict | None = None,
) -> WeatherResolution:
    target_date = get_event_date(event_ticker) or ""
    sample = markets[0] if markets else {}
    rules_primary = str(sample.get("rules_primary") or "")

    event_sources = None
    if event_meta:
        event_sources = event_meta.get("settlement_sources")
    series_sources = None
    if series_meta:
        series_sources = series_meta.get("settlement_sources")

    regime, certainty, evidence = detect_settlement_source_regime(
        rules_primary=rules_primary,
        settlement_sources=event_sources if isinstance(event_sources, list) else None,
        series_settlement_sources=series_sources if isinstance(series_sources, list) else None,
    )

    warnings: list[str] = []
    if regime == REGIME_WEATHER_COMPANY_CLINYC:
        station_id = "CLINYC"
        resolution_source = "The Weather Company (CLINYC)"
        warnings.append(
            "Live/current settlement uses Weather Company CLINYC; "
            "historical residual calibration uses NWS CLI KNYC."
        )
    elif regime == REGIME_NWS_CLI_KNYC:
        station_id = "KNYC"
        resolution_source = "NWS Climatological Report (Daily) — Central Park"
    elif regime == REGIME_CONFLICTING:
        station_id = "UNKNOWN"
        resolution_source = "conflicting settlement sources"
        warnings.append(
            "Conflicting NWS and Weather Company settlement signals; "
            "do not treat as a validated regime."
        )
    else:
        station_id = "KNYC"
        resolution_source = "assumed Central Park / KNYC (unverified)"
        certainty = "assumed"
        warnings.append(
            "Could not verify settlement source from rules; "
            "assuming Central Park / KNYC with low certainty."
        )
        evidence.append("fallback assumption: Central Park / KNYC")

    structure = "range_bucket" if is_range_bucket_event(markets) else "other"
    contracts = []
    for market in markets:
        bucket = parse_temperature_bucket(market)
        contracts.append(
            {
                "ticker": market.get("ticker"),
                "label": get_outcome_label(market),
                "result": market.get("result"),
                "bucket": bucket.to_dict() if bucket else None,
                "rules_primary": market.get("rules_primary"),
                "strike_type": market.get("strike_type"),
            }
        )

    return WeatherResolution(
        series_ticker=SERIES_TICKER,
        event_ticker=event_ticker,
        target_date=target_date,
        station_id=station_id,
        timezone="America/New_York",
        measurement="daily maximum temperature",
        unit="Fahrenheit",
        market_structure=structure,
        contracts=contracts,
        resolution_source=resolution_source,
        settlement_source_regime=regime,
        resolution_certainty=certainty,
        warnings=warnings,
        evidence=evidence,
    )


def season_for_month(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"
