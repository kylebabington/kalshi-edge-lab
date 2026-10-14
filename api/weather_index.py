"""Read-only index over saved weather artifacts for the HTTP API (RESEARCH_ONLY / NO_BET).

Reads saved PredictionSnapshots, their separate score files, checkpoint receipts, the prospective task log and
the tracked operational-replay CSV. Nothing here fetches upstream data, creates directories, captures, scores or
writes anything. Missing or invalid artifacts are reported as unavailable, never replaced by other data.

Settlement is reported on two levels that are never merged:
- ``event_settlement``: whether any valid score confirms the event's winning bucket (the event settled).
- ``displayed_snapshot_score``: whether the snapshot shown for the event (the latest one) has its own valid
  score. An older scored snapshot never makes an unscored latest snapshot appear scored.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from kalshi import cache
from research.weather import checkpoints as CK
from research.weather import resolution as RES
from research.weather import snapshots as SN
from research.weather.asof_observations import climate_day_bounds_utc

ET = ZoneInfo("America/New_York")
VIEWS = ("upcoming", "awaiting_settlement", "settled")
VIEW_ORDER = {v: i for i, v in enumerate(VIEWS)}
SORTS = ("default", "target_asc", "target_desc", "capture_desc", "settlement_desc")
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,63}$")
TASK_NAME = "KalshiEdgeLab-WeatherProspective"
CADENCE_MINUTES = 60
LOG_DIR = cache.REPO_ROOT / "data" / "logs" / "prospective"
REPLAY_CSV = cache.REPO_ROOT / "data" / "weather" / "calibration" / "gfs_operational_replay.csv"
SOURCE_SAVED = "saved prediction snapshots (local artifacts)"
LOG_LINE = re.compile(r"^(\S+) \[pid (\d+)\] cycle (start|end \(exit (-?\d+)\))")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_minutes(ts: Any, now: datetime) -> float | None:
    dt = _parse(ts)
    return None if dt is None else round((now - dt).total_seconds() / 60, 1)


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _finite(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


# ---------------------------------------------------------------- snapshot and score readers

def _probabilities(snap: dict) -> tuple[dict[str, float], int]:
    raw = snap.get("external_probability_distribution")
    if not isinstance(raw, dict) or not raw:
        raw = (snap.get("prediction") or {}).get("bucket_probabilities")
    out, invalid = {}, 0
    for k, v in (raw or {}).items() if isinstance(raw, dict) else []:
        f = _finite(v)
        if f is None or not 0.0 <= f <= 1.0:
            invalid += 1
        else:
            out[str(k)] = f
    return out, invalid


def _snapshot_summary(p: Path, snap: Any) -> dict | None:
    if not isinstance(snap, dict) or not snap.get("event_ticker") or not snap.get("snapshot_id"):
        return None
    pred = snap.get("prediction") or {}
    probs, invalid = _probabilities(snap)
    return {
        "snapshot_id": str(snap["snapshot_id"]),
        "event_ticker": str(snap["event_ticker"]),
        "target_date": snap.get("target_date") or pred.get("target_date"),
        "prediction_as_of": snap.get("prediction_as_of"),
        "created_at": snap.get("created_at"),
        "checkpoint_id": snap.get("checkpoint_id"),
        "prediction_status": snap.get("prediction_status") or pred.get("prediction_status"),
        "confidence_label": pred.get("confidence_label"),
        "expected_high": _finite(pred.get("expected_high")),
        "resolution_regime": snap.get("resolution_regime"),
        "transfer_status": snap.get("transfer_status") or pred.get("transfer_status"),
        "bucket_probabilities": probs,
        "invalid_probabilities": invalid,
    }


def _snapshots() -> list[dict]:
    root = SN.SNAPSHOT_ROOT
    if not root.exists():
        return []
    out = []
    for event_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for p in sorted(event_dir.glob("*.json")):
            if p.name == "latest.json":
                continue
            s = _snapshot_summary(p, _read_json(p))
            if s and s["event_ticker"] == event_dir.name:
                out.append(s)
    return out


def _scores() -> dict[str, dict]:
    root = SN.SCORE_ROOT
    out: dict[str, dict] = {}
    if not root.exists():
        return out
    for p in sorted(root.glob("*.json")):
        sc = _read_json(p)
        if isinstance(sc, dict) and sc.get("snapshot_id"):
            out[str(sc["snapshot_id"])] = sc
    return out


def _receipts() -> list[dict]:
    root = CK.CHECKPOINT_ROOT
    if not root.exists():
        return []
    out = []
    for event_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for p in sorted(event_dir.glob("*.json")):
            r = _read_json(p)
            if isinstance(r, dict):
                out.append(r)
    return out


def snapshot_score(snap: dict, sc: dict | None) -> dict:
    """Score state of one snapshot. Only a valid OK score with a finite Brier counts as scored."""
    if sc is None:
        return {"state": "not_scored", "reason": "no score file for this snapshot yet"}
    if sc.get("event_ticker") != snap["event_ticker"]:
        return {"state": "invalid", "reason": "score file names a different event"}
    status = sc.get("score_status")
    if status != SN.SCORE_STATUS_OK:
        return {"state": "unavailable", "reason": f"score status {status or 'missing'}",
                "scored_at": sc.get("scored_at")}
    brier, ll = _finite(sc.get("brier")), _finite(sc.get("log_loss"))
    if brier is None or not sc.get("winning_bucket"):
        return {"state": "invalid", "reason": "score marked OK but Brier or winning bucket is missing or not finite",
                "scored_at": sc.get("scored_at")}
    return {"state": "scored", "brier": brier, "log_loss": ll, "scored_at": sc.get("scored_at"),
            "probability_on_winner": _finite(sc.get("probability_on_winner")),
            "winning_bucket": sc.get("winning_bucket")}


def _event_settlement(snaps: list[dict], scores: dict[str, dict]) -> dict:
    confirmations = []
    for s in snaps:
        st = snapshot_score(s, scores.get(s["snapshot_id"]))
        if st["state"] == "scored":
            sc = scores[s["snapshot_id"]]
            confirmations.append({"winning_bucket": sc.get("winning_bucket"),
                                  "actual_high_f": _finite(sc.get("actual_high_f")),
                                  "source": sc.get("settlement_source"),
                                  "regime": sc.get("resolution_regime"),
                                  "scored_at": sc.get("scored_at"), "snapshot_id": s["snapshot_id"]})
    if not confirmations:
        return {"state": "not_confirmed", "reason": "no valid score confirms the winning bucket yet"}
    buckets = {c["winning_bucket"] for c in confirmations}
    if len(buckets) > 1:
        return {"state": "conflict", "reason": f"scores disagree on the winning bucket: {sorted(buckets)}"}
    first = min(confirmations, key=lambda c: c["scored_at"] or "")
    return {"state": "confirmed", "winning_bucket": first["winning_bucket"], "actual_high_f": first["actual_high_f"],
            "source": first["source"], "regime": first["regime"], "confirmed_at": first["scored_at"],
            "confirmed_by_snapshot_id": first["snapshot_id"], "confirming_scores": len(confirmations)}


def _view(target_date: str | None, settlement: dict, today: str) -> str:
    if settlement["state"] == "confirmed":
        return "settled"
    if target_date and target_date >= today:
        return "upcoming"
    return "awaiting_settlement"


def _event_item(ticker: str, snaps: list[dict], scores: dict[str, dict], receipts: list[dict], today: str) -> dict:
    snaps = sorted(snaps, key=lambda s: s["prediction_as_of"] or "")
    latest = snaps[-1]
    settlement = _event_settlement(snaps, scores)
    target = latest["target_date"]
    rs = [r for r in receipts if r.get("event_ticker") == ticker]
    scored = sum(1 for s in snaps if snapshot_score(s, scores.get(s["snapshot_id"]))["state"] == "scored")
    return {
        "category": "weather", "event_ticker": ticker, "target_date": target,
        "view": _view(target, settlement, today),
        "snapshot_count": len(snaps), "scored_snapshot_count": scored,
        "first_prediction_as_of": snaps[0]["prediction_as_of"],
        "displayed_snapshot": latest,
        "displayed_snapshot_score": snapshot_score(latest, scores.get(latest["snapshot_id"])),
        "event_settlement": settlement,
        "receipts": {"captured": sum(1 for r in rs if r.get("status") == CK.RECEIPT_STATUS_CAPTURED),
                     "missed": sum(1 for r in rs if r.get("status") == CK.RECEIPT_STATUS_MISSED)},
    }


def _sort(items: list[dict], sort: str) -> list[dict]:
    def latest(i):
        return i["displayed_snapshot"]["prediction_as_of"] or ""

    def settled(i):
        return i["event_settlement"].get("confirmed_at") or ""

    if sort == "target_asc":
        return sorted(items, key=lambda i: (i["target_date"] or "9999", i["event_ticker"]))
    if sort == "target_desc":
        return sorted(items, key=lambda i: (i["target_date"] or "", i["event_ticker"]), reverse=True)
    if sort == "capture_desc":
        return sorted(items, key=lambda i: (latest(i) != "", latest(i)), reverse=True)
    if sort == "settlement_desc":
        return sorted(items, key=lambda i: (settled(i) != "", settled(i)), reverse=True)
    # default: upcoming soonest first, then pending and settled most recent first
    upcoming = sorted((i for i in items if i["view"] == "upcoming"), key=lambda i: i["target_date"] or "")
    rest = sorted((i for i in items if i["view"] != "upcoming"), key=lambda i: i["target_date"] or "", reverse=True)
    return upcoming + sorted(rest, key=lambda i: VIEW_ORDER[i["view"]])


def all_events(now: datetime | None = None) -> list[dict]:
    now = now or _now()
    today = now.astimezone(ET).date().isoformat()
    snaps, scores, receipts = _snapshots(), _scores(), _receipts()
    by: dict[str, list[dict]] = {}
    for s in snaps:
        by.setdefault(s["event_ticker"], []).append(s)
    return [_event_item(t, v, scores, receipts, today) for t, v in by.items()]


def artifacts() -> dict:
    return {"snapshots": "present" if SN.SNAPSHOT_ROOT.exists() and any(SN.SNAPSHOT_ROOT.glob("*/*.json"))
            else "missing",
            "scores": "present" if SN.SCORE_ROOT.exists() and any(SN.SCORE_ROOT.glob("*.json")) else "missing"}


def saved_events(filters: dict, sort: str = "default", page: int = 1, page_size: int = 24) -> dict:
    now = _now()
    items = all_events(now)
    views = {v: sum(1 for i in items if i["view"] == v) for v in VIEWS}
    st, q = filters.get("status"), (filters.get("q") or "").strip().lower()
    sel = [i for i in items
           if (not st or st == "all" or i["view"] == st)
           and (not q or q in f"{i['event_ticker']} {i['target_date'] or ''}".lower())]
    sel = _sort(sel, sort if sort in SORTS else "default")
    page_size = max(1, min(page_size, 200))
    pages = max(1, -(-len(sel) // page_size))
    page = max(1, min(page, pages))
    return {"generated_at": _iso(now), "label": "Saved weather forecasts", "source": SOURCE_SAVED,
            "artifacts": artifacts(), "filters": {k: v for k, v in filters.items() if v}, "sort": sort,
            "total": len(sel), "page": page, "page_size": page_size, "pages": pages, "views": views,
            "items": sel[(page - 1) * page_size: page * page_size]}


# ---------------------------------------------------------------- prediction history (one event)

def bucket_definition(label: str, ticker: str | None) -> dict:
    """Saved definition of one bucket: its label, the integer bounds the label states (via the repo's bucket
    parser) and the market ticker the snapshot's quote sidecar saved for that label (if any)."""
    b = RES.parse_temperature_bucket({"yes_sub_title": label, "ticker": ticker})
    lo, hi = (b.lower_bound, b.upper_bound) if b else (None, None)
    parsed = b is not None
    key = f"{label}|{lo if lo is not None else '-'}|{hi if hi is not None else '-'}|{ticker or '-'}"
    return {"id": key, "label": label, "lower_f": lo, "upper_f": hi, "ticker": ticker, "bounds_parsed": parsed}


def _sidecar_tickers(snap: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in (snap.get("kalshi_quote_sidecar") or {}).get("markets") or []:
        if isinstance(m, dict) and isinstance(m.get("label"), str) and isinstance(m.get("ticker"), str):
            out[m["label"]] = m["ticker"]
    return out


def _raw_probabilities(snap: dict) -> dict[str, Any]:
    raw = snap.get("external_probability_distribution")
    if not isinstance(raw, dict) or not raw:
        raw = (snap.get("prediction") or {}).get("bucket_probabilities")
    return raw if isinstance(raw, dict) else {}


def _classify_obs_time(ts: Any, start: datetime, end: datetime) -> str:
    """Position of one observation time relative to the target's fixed-EST climate day. Missing or unparseable
    times, and times without a UTC offset, are ``unknown`` (never assumed to be on either side)."""
    if not isinstance(ts, str) or not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        return "unknown"
    if dt < start:
        return "before_day"
    if dt >= end:
        return "after_day"
    return "in_day"


def observation_summary(snap: dict, target_date: str | None) -> dict:
    ob = (snap.get("evidence") or {}).get("observations")
    prov = ((snap.get("prediction") or {}).get("provenance") or {})
    used = prov.get("live_has_observations")
    if not isinstance(ob, dict):
        return {"present": False, "used_by_prediction": used if isinstance(used, bool) else None}
    out: dict[str, Any] = {"present": True, "used_by_prediction": used if isinstance(used, bool) else None,
                           "station_id": ob.get("station_id"), "high_so_far_f": _finite(ob.get("high_so_far_f")),
                           "latest_temperature_f": _finite(ob.get("latest_temperature_f")),
                           "latest_timestamp": ob.get("latest_timestamp"),
                           "quality_status": ob.get("quality_status"), "freshness_status": ob.get("freshness_status")}
    try:
        start, end = climate_day_bounds_utc(str(target_date)) if target_date else (None, None)
    except ValueError:
        start, end = None, None
    if start is None:
        out.update(climate_day_bounds_utc=None, latest_position="unknown",
                   trajectory_points={"in_day": 0, "before_day": 0, "after_day": 0, "unknown": 0})
        return out
    traj = ob.get("trajectory") if isinstance(ob.get("trajectory"), list) else []
    counts = {"in_day": 0, "before_day": 0, "after_day": 0, "unknown": 0}
    for pt in traj:
        counts[_classify_obs_time(pt.get("timestamp") if isinstance(pt, dict) else None, start, end)] += 1
    out.update(climate_day_bounds_utc=[_iso(start), _iso(end)],
               latest_position=_classify_obs_time(ob.get("latest_timestamp"), start, end),
               trajectory_points=counts)
    return out


def run_summary(snap: dict) -> dict:
    pred = snap.get("prediction") or {}
    ev = snap.get("evidence") or {}
    gefs_meta = (ev.get("gefs") or {}).get("metadata") or {}
    return {"run_id": pred.get("run_id"), "forecast_run": pred.get("forecast_run"),
            "gfs_model_run_at": (ev.get("gfs") or {}).get("model_run_at"),
            "hrrr_model_run_at": (ev.get("hrrr") or {}).get("model_run_at"),
            "gefs_member_count": gefs_meta.get("member_count") if isinstance(gefs_meta.get("member_count"), int)
            else None,
            "calibration_artifact_id": snap.get("calibration_artifact_id"), "schema_version": snap.get("schema_version")}


def history_row(snap: dict, target_date: str | None) -> tuple[dict, list[dict]]:
    """Per-snapshot history fields plus the bucket definitions it saved. Probabilities that are missing, not
    finite or outside [0, 1] stay ``None`` with ``invalid`` set; nothing is interpolated."""
    tickers = _sidecar_tickers(snap)
    defs, probs = [], {}
    for label, v in _raw_probabilities(snap).items():
        d = bucket_definition(str(label), tickers.get(str(label)))
        f = _finite(v)
        ok = f is not None and 0.0 <= f <= 1.0
        probs[d["id"]] = {"p": f if ok else None, "invalid": not ok}
        defs.append(d)
    return {"probabilities": probs, "run": run_summary(snap), "observation": observation_summary(snap, target_date)}, defs


def _bucket_order(d: dict) -> tuple:
    lo, hi = d["lower_f"], d["upper_f"]
    return (lo if lo is not None else -10_000, hi if hi is not None else 10_000, d["label"], d["ticker"] or "")


def saved_event(ticker: str) -> dict | None:
    if not TICKER_RE.match(ticker or ""):
        return None
    now = _now()
    event_dir = SN.SNAPSHOT_ROOT / ticker
    if not event_dir.is_dir():
        return None
    raws, snaps = {}, []
    for p in sorted(event_dir.glob("*.json")):
        if p.name == "latest.json":
            continue
        raw = _read_json(p)
        s = _snapshot_summary(p, raw)
        if s and s["event_ticker"] == ticker:
            snaps.append(s)
            raws[s["snapshot_id"]] = raw
    if not snaps:
        return None
    scores, receipts = _scores(), _receipts()
    today = now.astimezone(ET).date().isoformat()
    item = _event_item(ticker, snaps, scores, receipts, today)
    buckets: dict[str, dict] = {}
    rows = []
    for s in sorted(snaps, key=lambda s: (s["prediction_as_of"] or "", s["snapshot_id"])):
        hist, defs = history_row(raws[s["snapshot_id"]], s["target_date"] or item["target_date"])
        for d in defs:
            b = buckets.setdefault(d["id"], {**d, "snapshots": 0, "valid_points": 0})
            b["snapshots"] += 1
            b["valid_points"] += 0 if hist["probabilities"][d["id"]]["invalid"] else 1
        rows.append({**{k: s[k] for k in ("snapshot_id", "prediction_as_of", "created_at", "checkpoint_id",
                                          "prediction_status", "confidence_label", "expected_high")},
                     "score": snapshot_score(s, scores.get(s["snapshot_id"])), **hist})
    ordered = sorted(buckets.values(), key=_bucket_order)
    labels: dict[str, int] = {}
    for b in ordered:
        labels[b["label"]] = labels.get(b["label"], 0) + 1
    for b in ordered:
        b["definition_variants"] = labels[b["label"]]
    return {"generated_at": _iso(now), "label": "Saved weather forecasts", "source": SOURCE_SAVED,
            "event": item, "snapshots": rows, "buckets": ordered,
            "history_note": "Saved model probabilities at each capture time. Points are not interpolated and are "
                            "not market prices. Buckets with the same label but different saved definitions are "
                            "separate series."}


# ---------------------------------------------------------------- collector health and forecast freshness

def _cycles(now: datetime, days: int = 3) -> list[dict]:
    """Cycle end lines from the prospective task log (UTC timestamps written by the task wrapper)."""
    if not LOG_DIR.exists():
        return []
    files = sorted(LOG_DIR.glob("*.log"))[-days:]
    out = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = LOG_LINE.match(line)
            if m and m.group(4) is not None:
                ts = _parse(m.group(1))
                if ts:
                    out.append({"finished_at": _iso(ts), "exit_code": int(m.group(4)), "_ts": ts})
    return sorted(out, key=lambda c: c["_ts"])


def _lock() -> dict:
    p = LOG_DIR / "cycle.lock"
    if not p.exists():
        return {"held": False, "state": "free"}
    owner = _read_json(p)
    return {"held": True, "state": "held",
            "acquired_at": owner.get("acquired_utc") if isinstance(owner, dict) else None}


def collector(task_query: Callable[[str], dict] | None = None) -> dict:
    now = _now()
    if task_query is None:
        from research.sports.ops import scheduler as SCH
        task_query = SCH.query_task
    cycles = _cycles(now)
    last = cycles[-1] if cycles else None
    ok = [c for c in cycles if c["exit_code"] == 0]
    day = [c for c in cycles if c["_ts"] >= now - timedelta(hours=24)]
    consecutive = 0
    for c in reversed(cycles):
        if c["exit_code"] == 0:
            break
        consecutive += 1
    snaps = _snapshots()
    receipts = _receipts()
    scores = list(_scores().values())
    captured = [r.get("actual_prediction_as_of") or r.get("created_at") for r in receipts
                if r.get("status") == CK.RECEIPT_STATUS_CAPTURED]
    missed_24h = [r for r in receipts if r.get("status") == CK.RECEIPT_STATUS_MISSED
                  and (_parse(r.get("created_at")) or now - timedelta(days=2)) >= now - timedelta(hours=24)]
    last_snap = max((s["prediction_as_of"] for s in snaps if s["prediction_as_of"]), default=None)
    last_receipt = max((t for t in captured if t), default=None)
    last_score = max((s.get("scored_at") for s in scores if s.get("scored_at")), default=None)
    return {
        "generated_at": _iso(now), "task_name": TASK_NAME, "cadence_minutes": CADENCE_MINUTES,
        "scheduler": task_query(TASK_NAME),
        "lock": _lock(),
        "log_observed": bool(cycles),
        "cycles": {
            "last_cycle_finished_at": last["finished_at"] if last else None,
            "last_cycle_exit_code": last["exit_code"] if last else None,
            "last_successful_cycle_at": ok[-1]["finished_at"] if ok else None,
            "minutes_since_last_successful_cycle": _age_minutes(ok[-1]["finished_at"], now) if ok else None,
            "cycles_24h": len(day), "failures_24h": sum(1 for c in day if c["exit_code"] != 0),
            "consecutive_failures": consecutive,
        },
        "forecast_freshness": {
            "last_snapshot_prediction_as_of": last_snap,
            "minutes_since_last_snapshot": _age_minutes(last_snap, now),
            "last_captured_checkpoint_at": last_receipt,
            "last_score_written_at": last_score,
            "minutes_since_last_score": _age_minutes(last_score, now),
            "missed_checkpoints_24h": len(missed_24h),
        },
        "limits": "Captures need the machine awake, logged in and online at each checkpoint. Missed checkpoints are "
                  "recorded as MISSED and never reconstructed from later downloads. Opening this page never starts "
                  "a cycle.",
    }


# ---------------------------------------------------------------- metric context (sample sizes and periods)

def metrics_context() -> dict:
    now = _now()
    scores = [s for s in _scores().values() if s.get("score_status") == SN.SCORE_STATUS_OK
              and _finite(s.get("brier")) is not None]
    dates = sorted(str(s["target_date"]) for s in scores if s.get("target_date"))
    replay = {"available": False, "missing": f"{REPLAY_CSV.name} not found"}
    if REPLAY_CSV.exists():
        try:
            with REPLAY_CSV.open(encoding="utf-8", errors="replace", newline="") as fh:
                rdates = sorted({r["target_date"] for r in csv.DictReader(fh) if r.get("target_date")})
            replay = {"available": True, "first_target_date": rdates[0] if rdates else None,
                      "last_target_date": rdates[-1] if rdates else None, "target_dates": len(rdates)}
        except (OSError, KeyError, csv.Error) as e:
            replay = {"available": False, "missing": f"{REPLAY_CSV.name} unreadable: {type(e).__name__}"}
    return {
        "generated_at": _iso(now),
        "prospective_snapshot_scores": {
            "kind": "prospective", "scored_snapshots": len(scores),
            "events": len({s.get("event_ticker") for s in scores}),
            "first_target_date": dates[0] if dates else None, "last_target_date": dates[-1] if dates else None,
        },
        "operational_replay": {"kind": "historical", **replay},
    }
