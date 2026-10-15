"""Normalized research evidence built only from data the repository already collects.

V1 sources: the saved weather snapshot (model output, GFS/GEFS/HRRR/NWS forecasts, KNYC observations,
NWS forecast discussion) and the saved sports capture record (model candidates, market reference,
Kalshi listing text). No web search, news, injury feeds or scraping.
"""

from __future__ import annotations

from typing import Any

from research.opportunities.models import Opportunity

from .models import ResearchEvidence

AFD_EXCERPT_CHARS = 2500
SPORTS_DATA_GAP = ("This system does not collect injury reports, lineups, roster news, weather at venue or "
                   "third-party odds. Any such factor is UNKNOWN.")


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "unavailable"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


class _Builder:
    def __init__(self) -> None:
        self.items: list[ResearchEvidence] = []

    def add(self, source: str, source_type: str, title: str, content: str, *, published_at: str | None = None,
            retrieved_at: str | None = None, url: str | None = None, reliability: str = "medium") -> None:
        if not content.strip():
            return
        self.items.append(ResearchEvidence(
            id=f"E{len(self.items) + 1}", source=source, source_type=source_type, title=title,
            content=content.strip(), published_at=published_at, retrieved_at=retrieved_at, url=url,
            reliability=reliability))


def _market_lines(opp: Opportunity) -> str:
    return "\n".join([
        f"YES bid {_fmt(opp.yes_bid, 2)}, YES ask {_fmt(opp.yes_ask, 2)}, mid {_fmt(opp.market_probability)}",
        f"Quote time: {opp.quote_as_of or 'unknown'} (saved at capture; not a live quote)",
    ])


def _model_lines(opp: Opportunity) -> list[str]:
    return [
        f"Model probability (YES): {_fmt(opp.model_probability)}",
        f"Best side vs executable price: {opp.side or 'none'}; probability edge {_fmt(opp.probability_edge)}",
        f"EV before fees per $1 contract: {_fmt(opp.expected_value_before_fees, 4)}; estimated fee "
        f"{_fmt(opp.estimated_fees, 4)}; EV after fees {_fmt(opp.expected_value_after_fees, 4)}",
        f"Prediction status: {opp.prediction_status}; prediction as of {opp.prediction_as_of}",
    ]


def weather_evidence(opp: Opportunity) -> list[ResearchEvidence]:
    ctx = opp.domain_context
    ev = ctx.get("evidence") or {}
    b = _Builder()
    pct = ctx.get("percentiles") or {}
    b.add("kalshi-edge-lab weather model (calibrated GFS residuals)", "model_output", "Statistical model output",
          "\n".join(_model_lines(opp) + [
              f"Contract: {opp.contract_label}",
              f"Expected high {_fmt(ctx.get('expected_high'), 1)} F; P10 {_fmt(pct.get('p10_high'), 1)}, "
              f"median {_fmt(pct.get('median_high'), 1)}, P90 {_fmt(pct.get('p90_high'), 1)}",
              f"Confidence {opp.confidence_label} ({_fmt(opp.confidence_score, 2)}); model agreement "
              f"{opp.model_agreement}; calibration {opp.calibration_quality}; sample N={opp.sample_size}",
              "Confidence reasons: " + "; ".join(ctx.get("confidence_reasons") or []),
              f"Settlement transfer validated: {ctx.get('transfer_validated')}; transfer status "
              f"{ctx.get('transfer_status')}; calibration method {ctx.get('calibration_method')}",
          ]), published_at=opp.prediction_as_of, reliability="internal_model")
    b.add("Kalshi (saved quote)", "market_data", "Market quote at capture", _market_lines(opp),
          published_at=opp.quote_as_of, reliability="high")
    for key in ("gfs", "gefs", "hrrr", "nws_forecast"):
        src = ev.get(key) or {}
        if not src:
            continue
        lines = [f"Forecast high {_fmt(src.get('forecast_high_f'), 1)} F",
                 f"Model run {src.get('model_run_at') or 'n/a'}; issued {src.get('issued_at') or 'n/a'}; "
                 f"available {src.get('available_at') or 'n/a'}",
                 f"Freshness {src.get('freshness_status')}; quality {src.get('quality_status')}"]
        if src.get("expected_high_f") is not None:
            lines.append(f"Expected high {_fmt(src.get('expected_high_f'), 1)} F")
        runs = [r for r in src.get("recent_runs") or [] if isinstance(r, dict)]
        if runs:
            lines.append("Recent runs: " + ", ".join(
                f"{r.get('run_init')}: {_fmt(r.get('forecast_high_f'), 1)} F" for r in runs[-4:]))
        if src.get("run_to_run_trend_f") is not None:
            lines.append(f"Run-to-run trend {_fmt(src.get('run_to_run_trend_f'), 1)} F")
        b.add(src.get("source_name") or key, "forecast_model", f"{src.get('source_name') or key} forecast",
              "\n".join(lines), published_at=src.get("available_at") or src.get("model_run_at"),
              retrieved_at=src.get("retrieved_at"), reliability="high")
    agree = ev.get("agreement") or {}
    if agree:
        sources = agree.get("sources") or {}
        b.add("kalshi-edge-lab source comparison", "derived", "Forecast source agreement (descriptive)",
              "\n".join([f"Disagreement: {agree.get('disagreement_label')}; max-min "
                         f"{_fmt(agree.get('max_min_disagreement_f'), 1)} F",
                         "Sources: " + ", ".join(f"{k}={_fmt(v, 1)}" for k, v in sources.items())]),
              reliability="high")
    obs = ev.get("observations") or {}
    if obs:
        b.add(f"NWS observations ({obs.get('station_id') or 'KNYC'})", "observation", "Observed temperatures",
              "\n".join([f"Latest {_fmt(obs.get('latest_temperature_f'), 1)} F at {obs.get('latest_timestamp')}",
                         f"High so far {_fmt(obs.get('high_so_far_f'), 1)} F at {obs.get('time_of_high')}",
                         f"Change 1 h {_fmt(obs.get('change_1h_f'), 1)} F; 3 h {_fmt(obs.get('change_3h_f'), 1)} F"]),
              published_at=obs.get("latest_timestamp"), retrieved_at=obs.get("retrieved_at"), reliability="high")
    afd = ev.get("nws_discussion") or {}
    if afd.get("raw_text") or afd.get("key_messages"):
        parts = []
        for field_name in ("temperature_discussion", "confidence_wording", "model_disagreement_mentions",
                           "forecast_change_reasons", "frontal_timing", "cloud_cover_discussion"):
            lines = [str(x) for x in afd.get(field_name) or []][:6]
            if lines:
                parts.append(f"{field_name}: " + " | ".join(lines))
        raw = str(afd.get("raw_text") or "")
        if raw:
            parts.append("Excerpt:\n" + raw[:AFD_EXCERPT_CHARS])
        pid = afd.get("product_id")
        b.add(f"NWS Area Forecast Discussion ({afd.get('wfo') or 'OKX'})", "forecast_discussion",
              "NWS forecast discussion", "\n".join(parts), published_at=afd.get("issued_at"),
              retrieved_at=afd.get("retrieved_at"),
              url=f"https://api.weather.gov/products/{pid}" if pid else None, reliability="high")
    if opp.warnings:
        b.add("kalshi-edge-lab quality checks", "methodology", "Known methodology and data warnings",
              "\n".join(opp.warnings), reliability="high")
    return b.items


def sports_evidence(opp: Opportunity) -> list[ResearchEvidence]:
    ctx = opp.domain_context
    b = _Builder()
    cands = ctx.get("candidates") or {}
    why = ctx.get("candidate_unavailable") or {}
    cand_lines = [f"{k}: {_fmt(v)}" if v is not None else f"{k}: unavailable ({why.get(k) or 'no reason'})"
                  for k, v in cands.items()]
    b.add("kalshi-edge-lab sports model (frozen Elo/score model, optional calibration)", "model_output",
          "Statistical model output", "\n".join(_model_lines(opp) + [
              f"Model source: {ctx.get('model_source')} (frozen model: {ctx.get('frozen_model')})",
              "Candidates: " + "; ".join(cand_lines),
              "The blend candidate already includes the market price and is not used as the model probability.",
          ]), published_at=opp.prediction_as_of, reliability="internal_model")
    b.add("Kalshi hourly candle (saved)", "market_data", "Market reference quote", "\n".join([
        _market_lines(opp), f"Candle age at prediction: {_fmt(ctx.get('quote_age_s_at_prediction'))} s"]),
        published_at=opp.quote_as_of, reliability="high")
    b.add("Kalshi market listing (saved evidence)", "event_info", "Event and contract", "\n".join(x for x in [
        f"Event: {ctx.get('event_title') or opp.event_key}",
        f"Sport: {ctx.get('sport')}; competition: {ctx.get('competition')}; family: {ctx.get('family')}",
        f"Contract: {opp.contract_label or opp.ticker}", f"Scheduled start: {ctx.get('event_start')}",
        f"Rules: {ctx.get('rules')}" if ctx.get("rules") else "",
    ] if x), reliability="high")
    b.add("kalshi-edge-lab capture record", "methodology", "Capture timing and comparability", "\n".join(x for x in [
        ctx.get("timing_label"), ctx.get("comparability_note"),
        f"Horizon {ctx.get('horizon_minutes')} min before start; cohort {ctx.get('cohort')}",
    ] if x), reliability="high")
    if opp.warnings:
        b.add("kalshi-edge-lab quality checks", "methodology", "Known methodology and data warnings",
              "\n".join(opp.warnings), reliability="high")
    b.add("kalshi-edge-lab", "limitation", "Data not collected", SPORTS_DATA_GAP, reliability="high")
    return b.items


def gather_evidence(opp: Opportunity) -> list[ResearchEvidence]:
    return weather_evidence(opp) if opp.category == "weather" else sports_evidence(opp)
