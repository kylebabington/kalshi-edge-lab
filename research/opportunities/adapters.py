"""Read-only adapters: saved prediction artifacts -> unpriced :class:`Opportunity` objects.

* Weather: ``data/weather/snapshots/<event>/latest.json`` written by the prospective collector
  (prediction + ``kalshi_quote_sidecar`` captured at the same time).
* Sports: accepted ``sports_phase2_capture_v2`` checkpoint records for events that have not started.

No upstream fetches and no writes. Non-OK predictions are kept (later marked BLOCKED) so the API
can show why they are not ranked.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kalshi import cache

from .models import Opportunity

NY = ZoneInfo("America/New_York")
_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})$")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN -> None


def _ticker_date(event_ticker: str) -> date | None:
    m = _TICKER_DATE.search(event_ticker)
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def _series(ticker: str) -> str | None:
    return ticker.split("-", 1)[0] if ticker else None


# ---------------------------------------------------------------- weather

def weather_snapshot_root() -> Path:
    from research.weather.snapshots import SNAPSHOT_ROOT
    return SNAPSHOT_ROOT


def _latest_weather_snapshots(root: Path, today: date) -> list[dict]:
    out = []
    if not root.exists():
        return out
    for event_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        d = _ticker_date(event_dir.name)
        if d is not None and d < today:
            continue
        snap = cache.read_json(event_dir / "latest.json", default=None)
        if isinstance(snap, dict):
            out.append(snap)
    return out


def weather_opportunities(now: datetime, *, root: Path | None = None) -> list[Opportunity]:
    today = now.astimezone(NY).date()
    out: list[Opportunity] = []
    for snap in _latest_weather_snapshots(root or weather_snapshot_root(), today):
        out.extend(_weather_from_snapshot(snap, now, today))
    return out


def _weather_from_snapshot(snap: dict, now: datetime, today: date) -> list[Opportunity]:
    event = str(snap.get("event_ticker") or "")
    pred = snap.get("prediction") or {}
    conf = snap.get("confidence") or {}
    research = snap.get("research_status") or {}
    provenance = pred.get("provenance") or {}
    probs = snap.get("external_probability_distribution") or pred.get("bucket_probabilities") or {}
    status = str(snap.get("prediction_status") or pred.get("prediction_status") or "UNKNOWN")
    as_of = _parse_dt(snap.get("prediction_as_of"))
    age = (now - as_of).total_seconds() if as_of else None
    target = snap.get("target_date")
    try:
        target_d = date.fromisoformat(str(target)) if target else _ticker_date(event)
    except ValueError:
        target_d = _ticker_date(event)
    transfer_validated = pred.get("settlement_source_transfer_validated",
                                  research.get("settlement_source_transfer_validated"))
    pool = provenance.get("residual_pool_meta") or {}
    sample = pred.get("residual_sample_size", pool.get("N"))
    warnings = list(dict.fromkeys(str(w) for w in snap.get("warnings") or []))

    out = []
    for m in (snap.get("kalshi_quote_sidecar") or {}).get("markets") or []:
        ticker = str(m.get("ticker") or "")
        if not ticker:
            continue
        label = m.get("label")
        p = _num(probs.get(label)) if status == "OK" and label in probs else None
        bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
        mid = _num(m.get("mid_dollars"))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2
        not_open = None
        if target_d is not None and target_d < today:
            not_open = f"target date {target_d} has passed"
        elif m.get("result"):
            not_open = f"market already settled ({m.get('result')})"
        elif m.get("status") not in (None, "active", "open"):
            not_open = f"market status at capture: {m.get('status')}"
        out.append(Opportunity(
            id=f"weather:{ticker}", ticker=ticker, event_ticker=event, event_key=event, category="weather",
            title=f"NYC high temperature {target or ''}: {label or ticker}".strip(), contract_label=label,
            series=_series(ticker), model_probability=p, market_probability=mid, yes_bid=bid, yes_ask=ask,
            spread=(ask - bid) if bid is not None and ask is not None else None,
            confidence_score=_num(conf.get("score", pred.get("confidence_score"))),
            confidence_label=conf.get("label") or pred.get("confidence_label"),
            confidence_source="weather_model",
            model_agreement=conf.get("model_agreement") or pred.get("model_agreement"),
            sample_size=int(sample) if isinstance(sample, (int, float)) else None,
            calibration_quality=conf.get("calibration_quality") or pred.get("calibration_quality"),
            prediction_as_of=snap.get("prediction_as_of"), quote_as_of=snap.get("prediction_as_of"),
            data_age_seconds=age, prediction_status=status,
            domain_context={
                "event_open": not_open is None, "not_open_reason": not_open,
                "target_date": target, "transfer_validated": transfer_validated,
                "transfer_status": snap.get("transfer_status") or pred.get("transfer_status")
                or research.get("transfer_status"),
                "calibration_method": snap.get("calibration_method") or pred.get("calibration_method"),
                "target_regime_match": research.get("target_regime_match"),
                "confidence_reasons": list(provenance.get("confidence_reasons") or []),
                "source_warnings": warnings, "checkpoint_id": snap.get("checkpoint_id"),
                "expected_high": pred.get("expected_high"),
                "percentiles": {k: pred.get(k) for k in ("p10_high", "p25_high", "median_high",
                                                         "p75_high", "p90_high")},
                "bucket_probabilities": dict(probs) if status == "OK" else {},
                "evidence": snap.get("evidence") or {},
            },
            source_ref={"kind": "weather_snapshot", "snapshot_id": snap.get("snapshot_id")},
        ))
    return out


# ---------------------------------------------------------------- sports

def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sports_opportunities(now: datetime) -> list[Opportunity]:
    from research.sports import service as S

    out: list[Opportunity] = []
    now_ts = now.timestamp()
    for s in S.records():
        if "unreadable" in s or s.get("status") != "WINDOW_CAPTURE":
            continue
        start = _parse_dt(s.get("start"))
        if start is None or start.timestamp() <= now_ts:
            continue
        rec = _read(s["_path"])
        if rec is None:
            continue
        title = None
        desc: dict = {}
        try:
            title = S.event_title(s).get("title")
            desc, _ = S._market_descriptions(f"{s['_path']}::{s['run_id']}")
        except Exception:  # titles are presentation only; missing evidence must not hide the market
            pass
        out.extend(_sports_from_record(rec, s, now, start, title, desc))
    return out


def _sports_from_record(rec: dict, s: dict, now: datetime, start: datetime, title: str | None,
                        desc: dict) -> list[Opportunity]:
    as_of = _parse_dt(rec.get("prediction_as_of"))
    pred_age = (now - as_of).total_seconds() if as_of else None
    out = []
    for c in rec.get("contracts") or []:
        ticker = c.get("ticker")
        if not ticker:
            continue
        cands = c.get("candidates") or {}
        probs = {k: _num((cands.get(k) or {}).get("p")) for k in ("frozen", "calibrated", "blend")}
        why = {k: (cands.get(k) or {}).get("unavailable") for k in ("frozen", "calibrated", "blend", "market")}
        market = cands.get("market") or {}
        model_source = "calibrated" if probs["calibrated"] is not None else (
            "frozen" if probs["frozen"] is not None else None)
        p = probs[model_source] if model_source else None
        bid, ask = _num(market.get("bid")), _num(market.get("ask"))
        candle_end = _parse_dt(market.get("candle_end"))
        quote_age = (now - candle_end).total_seconds() if candle_end else None
        ages = [a for a in (pred_age, quote_age) if a is not None]
        d = desc.get(ticker) or {}
        label = d.get("yes_sub_title") or d.get("title")
        out.append(Opportunity(
            id=f"sports:{ticker}", ticker=ticker, event_ticker=(rec.get("kalshi_event_tickers") or [""])[0],
            event_key=s["event_id"], category="sports",
            title=" - ".join(x for x in (title or s["event_id"], label) if x), contract_label=label,
            series=c.get("series") or _series(ticker), model_probability=p,
            market_probability=_num(market.get("p")), yes_bid=bid, yes_ask=ask,
            spread=(ask - bid) if bid is not None and ask is not None else None,
            confidence_source=None, sample_size=None,
            calibration_quality="calibrated" if probs["calibrated"] is not None else "frozen_only",
            model_agreement=_sports_agreement(probs["frozen"], probs["calibrated"]),
            prediction_as_of=rec.get("prediction_as_of"),
            quote_as_of=candle_end.isoformat() if candle_end else None,
            data_age_seconds=max(ages) if ages else None,
            prediction_status="OK" if p is not None and c.get("status") == "ok" else "UNAVAILABLE",
            domain_context={
                "event_open": True, "event_start": s.get("start"), "sport": s.get("sport"),
                "competition": s.get("competition"), "capture_status": s.get("status"),
                "checksum_ok": bool(s.get("checksum_ok")), "contract_status": c.get("status"),
                "contract_reason": c.get("reason"), "cohort": c.get("cohort"), "family": c.get("family"),
                "kind": c.get("kind"), "model_source": model_source,
                "frozen_model": (cands.get("frozen") or {}).get("model"),
                "candidates": probs, "candidate_unavailable": why,
                "quote_age_s_at_prediction": market.get("age_s"), "horizon_minutes": rec.get("horizon_minutes"),
                "rules": d.get("rules_primary"), "event_title": title,
                "comparability_note": rec.get("comparability_note"), "timing_label": rec.get("timing_label"),
            },
            source_ref={"kind": "sports_capture", "event_id": s["event_id"], "run_id": rec.get("run_id")},
        ))
    return out


def _sports_agreement(frozen: float | None, calibrated: float | None) -> str | None:
    if frozen is None or calibrated is None:
        return None
    diff = abs(frozen - calibrated)
    return "HIGH" if diff <= 0.05 else "MODERATE" if diff <= 0.10 else "LOW"
