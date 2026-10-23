"""Read-only Research Hub catalog (RESEARCH_ONLY / NO_BET).

One registry entry per frozen Weather or Sports experiment. Descriptive text (title, question,
reference, limitations) is a short hub summary of the tracked protocol and docs; the protocol file
stays authoritative and is returned verbatim (compacted) as read-only frozen settings.

Values are read from files, never invented: anything that cannot be established is ``None`` with a
reason. Catalog cards only stat files, read small JSON/CSV summaries (cached by size and mtime), count
files in local directories and reuse the cached sports presentation layer. No upstream fetches, model
runs, report writing, directory creation or evidence-body reads. Integrity verification (hashing)
runs only in the detail view and only where a manifest exists.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research import status_records as SR

REPO_ROOT = SR.REPO_ROOT
RESULTS_ROOT = REPO_ROOT / "data" / "results"
PHASE7_ROOT = REPO_ROOT / "data" / "weather" / "phase7"
SPORTS_PROTOCOLS = REPO_ROOT / "research" / "sports" / "protocols"
WEATHER_HYPOTHESES = REPO_ROOT / "research" / "weather" / "hypotheses"
DOCS = REPO_ROOT / "docs" / "research"

CATEGORIES = ("weather", "sports")
MODES = ("historical_replay", "manual_pilot", "automatic_collection")
MODE_LABELS = {
    "historical_replay": "Historical replay",
    "manual_pilot": "Manual pilot",
    "automatic_collection": "Automatic collection",
}
SUMMARY_NOTE = "Hub summary of the tracked protocol and docs. The protocol file is authoritative."
WEATHER_CADENCE_MIN = 60

_cache_lock = threading.Lock()
_file_cache: dict[tuple[str, str], tuple[tuple[int, int], Any]] = {}


def _sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached(kind: str, p: Path, build: Callable[[Path], Any]) -> Any:
    sig = _sig(p)
    if sig is None:
        return None
    key = (kind, str(p))
    with _cache_lock:
        hit = _file_cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    value = build(p)
    with _cache_lock:
        _file_cache[key] = (sig, value)
    return value


def clear_caches() -> None:
    with _cache_lock:
        _file_cache.clear()


def _parse_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _INVALID


_INVALID = object()


def _json(p: Path) -> Any:
    """Parsed JSON, ``None`` when missing, ``_INVALID`` when unparseable."""
    return _cached("json", p, _parse_json)


def _json_dict(p: Path) -> dict | None:
    v = _json(p)
    return v if isinstance(v, dict) else None


def _csv_summary(p: Path, date_col: str | None = None) -> dict | None:
    def build(path: Path) -> dict:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except (OSError, ValueError, csv.Error):
            return {"rows": None, "dates": None}
        dates = None
        if date_col:
            ds = sorted({r[date_col] for r in rows if r.get(date_col)})
            dates = (ds[0], ds[-1], len(ds)) if ds else None
        return {"rows": len(rows), "dates": dates}

    return _cached(f"csv:{date_col}", p, build)


def _file_count(d: Path) -> int | None:
    """Number of files under ``d`` (presence only; nothing is parsed). ``None`` when absent."""
    if not d.is_dir():
        return None
    n = 0
    for _, _, files in os.walk(d):
        n += len(files)
    return n


def _rel(p: Path) -> str:
    return SR.rel(p)


def _sample(label: str, value: Any, basis: str, reason: str | None = None) -> dict:
    return {"label": label, "value": value, "basis": basis, "reason": None if value is not None else (reason or basis)}


def _result(label: str, *, kind: str, available: bool, href: str | None = None, path: Path | None = None,
            reason: str | None = None) -> dict:
    return {
        "label": label,
        "kind": kind,
        "available": available,
        "href": href,
        "path": _rel(path) if path else None,
        "as_of": SR.mtime_iso(path) if (path and available) else None,
        "reason": None if available else (reason or "not found on this machine"),
    }


def _source(label: str, path: Path, tracked: bool) -> dict:
    exists = path.exists()
    return {
        "label": label,
        "path": _rel(path),
        "tracked": tracked,
        "exists": exists,
        "as_of": SR.mtime_iso(path) if exists and path.is_file() else None,
    }


def _period(label: str, start: Any, end: Any, basis: str) -> dict:
    return {"label": label, "start": start, "end": end, "basis": basis}


def _compact(value: Any, limit: int = 2000) -> Any:
    """Protocol JSON for display: large nested blocks are summarised, never altered."""
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for k, v in value.items():
        text = json.dumps(v, ensure_ascii=False)
        if len(text) > limit:
            entries = len(v) if isinstance(v, (dict, list)) else None
            out[k] = {"omitted_for_display": True, "entries": entries, "size_bytes": len(text.encode("utf-8"))}
        else:
            out[k] = v
    return out


def _policy_records(proto: dict | None, basis: str) -> list[dict]:
    if not isinstance(proto, dict):
        return []
    out = []
    status = proto.get("status") if isinstance(proto.get("status"), dict) else {}
    if proto.get("research_only") is True or status.get("mode") == "RESEARCH_ONLY":
        out.append(SR.policy("Mode", "RESEARCH_ONLY", basis))
    if proto.get("no_bet") is True or status.get("decision") == "NO_BET":
        out.append(SR.policy("Decision", "NO_BET", basis))
    if status.get("shadow") == "SHADOW ONLY" or proto.get("status") == "shadow":
        out.append(SR.policy("Candidate", "SHADOW_ONLY", basis))
    return out


def _protocol_evidence(label: str, path: Path) -> dict:
    v = _json(path)
    return SR.json_evidence(label, path, parsed=v if isinstance(v, dict) else None) if v is not _INVALID else \
        SR.record("evidence", "invalid", label, "protocol file present but unparseable", source=_rel(path))


def _integrity_not_checked(detail: bool) -> dict:
    if detail:
        return SR.not_checked("integrity", "Integrity", "No verification is run by the web API for this experiment")
    return SR.not_checked("integrity", "Integrity", "Not checked for catalog cards; see the experiment page")


# ---------------------------------------------------------------- sports

_SPORTS_HISTORICAL = {
    "sports.phase1_v1": {
        "protocol": "sports_phase1_v1",
        "title": "Sports Phase 1 v1: frozen models vs naive baselines",
        "version": "v1",
        "question": "On 2026 test-period contracts, did the frozen per-competition Phase 1 models have lower "
                    "Brier score and log loss than naive baselines, with the Kalshi market reference reported "
                    "separately?",
        "reference": "Naive baselines; Kalshi hourly-candle bid/ask midpoint at the cutoff as a market reference "
                     "(frozen rule).",
        "candidates": [
            {"id": "model", "label": "Frozen Phase 1 model",
             "description": "Per-competition model (Elo for game winners, score and field models elsewhere); "
                            "grids selected on training data only."},
            {"id": "naive", "label": "Naive baseline", "description": "Reference forecast without model inputs."},
        ],
        "limitations": [
            "Superseded by sports_phase1_v2 (a retrospective evaluation correction).",
            "Retrospective: the 2026 test period was evaluated after the fact.",
            "Interval-based better/unclear/worse classes are exploratory and unadjusted for multiple comparisons.",
            "RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.",
        ],
    },
    "sports.phase1_v2": {
        "protocol": "sports_phase1_v2",
        "title": "Sports Phase 1 v2: retrospective correction",
        "version": "v2",
        "question": "Re-evaluating the same frozen Phase 1 models with corrected cohort and contract exclusions: "
                    "do they beat naive baselines on 2026 test contracts?",
        "reference": "Naive baselines; Kalshi market reference reported separately.",
        "candidates": [
            {"id": "model", "label": "Frozen Phase 1 model",
             "description": "Parameters copied verbatim from sports_phase1_v1 (no refitting, no tuning)."},
            {"id": "naive", "label": "Naive baseline", "description": "Reference forecast without model inputs."},
        ],
        "limitations": [
            "The 2026 test period was already examined in v1; v2 is a correction, not an untouched holdout.",
            "Interval-based classes are exploratory and unadjusted for multiple comparisons.",
            "RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.",
        ],
    },
    "sports.phase2_v1": {
        "protocol": "sports_phase2_v1",
        "title": "Sports Phase 2 v1: paired candidate comparisons",
        "version": "v1",
        "question": "Within each competition and contract family, do recalibrated or blended candidates improve on "
                    "the frozen Phase 1 model and the market reference on the same contracts?",
        "reference": "Frozen Phase 1 model and the Kalshi market reference (paired, same contracts).",
        "limitations": [
            "2026 (EVAL) was already examined in Phase 1; Phase 2 scores on it are retrospective development "
            "results, not confirmation.",
            "Comparisons below the minimum matched clusters are descriptive point values only.",
            "RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.",
        ],
    },
}

_SPORTS_RESULTS = {
    "sports_phase1_v1": ("sports_phase1_results_v1.csv", "sports_phase1_results_v1.md", "sports_phase1"),
    "sports_phase1_v2": ("sports_phase1_results_v2.csv", "sports_phase1_results_v2.md", "sports_phase1"),
    "sports_phase2_v1": ("sports_phase2_results_v1.csv", "sports_phase2_results_v1.md", "sports_phase2"),
}


def _candidates_from(proto: dict | None, key: str = "candidates") -> list[dict] | None:
    block = (proto or {}).get(key)
    if isinstance(block, dict) and block:
        return [{"id": k, "label": k, "description": str(v)} for k, v in block.items()]
    return None


def _cohort_rules(proto: dict | None) -> list[dict]:
    p = proto or {}
    out: list[dict] = []
    if isinstance(p.get("cohorts"), dict):
        out += [{"name": k, "rule": str(v)} for k, v in p["cohorts"].items()]
    gate = p.get("reconciliation_gate")
    if isinstance(gate, dict):
        cohorts = gate.get("cohorts")
        if isinstance(cohorts, dict):
            out += [{"name": k, "rule": str(v)} for k, v in cohorts.items()]
        rest = {k: v for k, v in gate.items() if k != "cohorts"}
        if rest:
            out.append({"name": "reconciliation_gate", "rule": json.dumps(rest)})
    if isinstance(p.get("exclusions"), list):
        out += [{"name": "exclusion", "rule": str(x)} for x in p["exclusions"]]
    return out


def _sports_historical(eid: str, detail: bool) -> dict:
    spec = _SPORTS_HISTORICAL[eid]
    pid = spec["protocol"]
    proto_path = SPORTS_PROTOCOLS / f"{pid}.json"
    proto = _json_dict(proto_path)
    art = _json_dict(SPORTS_PROTOCOLS / f"{pid}.artifacts.json")
    csv_name, report, results_dir = _SPORTS_RESULTS[pid]
    csv_path = DOCS / csv_name
    csv_info = _csv_summary(csv_path)
    local_metrics = RESULTS_ROOT / results_dir / pid / "metrics.json"

    from research.sports import evaluate as EV
    from research.sports.service import _test_period

    superseded_by = {v: k for k, v in EV.PARENT.items()}.get(pid)
    lifecycle = (
        {"value": f"superseded by {superseded_by}", "basis": "supersedes field of the newer protocol"}
        if superseded_by else {"value": "current", "basis": "no newer protocol supersedes it"}
    )
    period = _test_period(proto) if proto else None
    journals = ((art or {}).get("summary") or {}).get("prediction_journals")
    samples = [
        _sample("Result rows (tracked summary CSV)", (csv_info or {}).get("rows"),
                f"rows in docs/research/{csv_name}", "tracked summary CSV not found"),
        _sample("Prediction journals", journals, f"summary.prediction_journals in {pid}.artifacts.json",
                "artifact manifest has no prediction_journals count"),
    ]
    results = [
        _result("Results summary (CSV)", kind="tracked", available=csv_path.is_file(), path=csv_path),
        _result("Results report", kind="tracked", available=(DOCS / report).is_file(), path=DOCS / report),
        _result("Local metrics artifact", kind="local", available=local_metrics.is_file(), path=local_metrics),
        _result("Historical results view", kind="api", available=csv_path.is_file(),
                href=f"/sports?tab=historical&protocol={pid}", reason="tracked summary CSV not found"),
    ]
    status = {
        "implementation": SR.implementation(
            "Phase 1 evaluation" if "phase1" in pid else "Phase 2 pipeline",
            ["research/sports/evaluate.py"] if "phase1" in pid else ["research/sports/phase2/pipeline.py"],
        ),
        "evidence": [
            _protocol_evidence("Frozen protocol", proto_path),
            SR.file_evidence("Tracked results CSV", csv_path, what="tracked summary"),
            SR.file_evidence("Local metrics", local_metrics, what="local artifact"),
        ],
        "operations": SR.record("operations", "not_applicable", "Operations",
                                "Historical replay; no ongoing collection", reason="Historical replay; no ongoing collection"),
        "integrity": _integrity_not_checked(False),
        "policy": _policy_records(proto, f"{pid}.json"),
    }
    card = {
        "lifecycle": lifecycle,
        "frozen_at": (proto or {}).get("frozen_at"),
        "period": _period(period["label"], period["start"], period["end"], f"{pid}.json splits/periods")
        if period else None,
        "period_reason": None if period else "protocol has no test or evaluation period",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card

    from research.sports.service import HISTORICAL, _historical_source

    h = next(x for x in HISTORICAL if x["protocol"] == pid)
    src = _historical_source(h)
    status["integrity"] = _sports_integrity(src)
    candidates = spec.get("candidates") or _candidates_from(proto) or []
    rest = [
        _source("Results report", DOCS / report, True),
        *[_source(f"Addendum {a}", DOCS / a, True) for a in h.get("addenda", [])],
        _source("Docs manifest", SPORTS_PROTOCOLS / f"{pid}.docs_portable_v1.json", True),
        _source("Artifact manifest", SPORTS_PROTOCOLS / f"{pid}.artifacts.json", True),
        _source("Local metrics", local_metrics, False),
        _source("Local prediction journals", RESULTS_ROOT / results_dir / pid, False),
    ]
    card.update({
        "question": spec["question"],
        "reference": spec["reference"],
        "candidates": candidates,
        "cohort_rules": _cohort_rules(proto),
        "limitations": spec["limitations"],
        "mode_blocks": [
            _mode("historical_replay", True,
                  "Frozen retrospective evaluation on saved historical data.", samples, card["period"]),
            _mode("manual_pilot", False, "Not part of this protocol."),
            _mode("automatic_collection", False,
                  "Not part of this protocol. Prospective sports capture is sports.phase2_capture_v2."),
        ],
        "sources": [_source("Frozen protocol", proto_path, True), _source("Results CSV", csv_path, True), *rest],
        "frozen_settings": _compact(proto) if proto else None,
        "links": [
            {"label": "Historical results", "href": f"/sports?tab=historical&protocol={pid}"},
        ],
    })
    return card


def _sports_integrity(src: dict) -> dict:
    tracked = src.get("tracked_csv")
    local = src.get("local_artifacts")
    if tracked == "verified against tracked docs manifest" and local == "verified":
        state, reason = "verified", None
    elif (tracked and "DIFFERS" in tracked) or (local and "DIFFERS" in str(local)):
        state, reason = "failed", f"tracked CSV: {tracked}; local metrics: {local}"
    elif tracked == "verified against tracked docs manifest":
        state, reason = "verified", None
    else:
        state, reason = "not_checked", f"tracked CSV: {tracked}"
    rec = SR.record(
        "integrity", state, "Integrity",
        f"SHA-256 checked in this request. Tracked CSV: {tracked}; local metrics: {local}",
        reason=reason,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
    rec["checks"] = {"tracked_csv": tracked, "local_artifacts": local}
    return rec


def _sports_operations() -> dict:
    from research.sports.ops import collector as CO

    st = _json_dict(CO.STATUS_PATH)
    if not st:
        return SR.unknown("operations", "Sports collector", "No collector status file on this machine")
    last = st.get("last_cycle") or {}
    finished, result = last.get("finished_at"), last.get("result")
    age_h = SR.age_hours(finished, datetime.now(timezone.utc))
    limit_h = (2 * CO.CADENCE_MIN + 5) / 60.0
    basis = f"collector_status.json last cycle (stale after {2 * CO.CADENCE_MIN + 5} min)"
    if result is None or age_h is None:
        return SR.unknown("operations", "Sports collector", "Status file has no last cycle result or time",
                          source="data/logs/sports/collector_status.json")
    if result != "ok":
        return SR.record("operations", "failed", "Sports collector", basis, as_of=finished,
                         source="data/logs/sports/collector_status.json", reason=f"Last cycle result: {result}")
    state = "recent_success" if age_h <= limit_h else "stale"
    return SR.record("operations", state, "Sports collector", basis, as_of=finished,
                     source="data/logs/sports/collector_status.json",
                     reason=None if state == "recent_success" else f"Last successful cycle {age_h * 60:.0f} min ago")


def _sports_capture(detail: bool) -> dict:
    from research.sports import service as S
    from research.sports.live import config as LC
    from research.sports.scoring import config as SCC

    pid = "sports_phase2_capture_v2"
    proto_path = SPORTS_PROTOCOLS / f"{pid}.json"
    proto = _json_dict(proto_path)
    if not _sports_index_ready(S, LC):
        return _sports_capture_loading(proto, proto_path, detail)
    summary = S.get_summary()
    counts = summary.get("counts") or {}
    by_mode = (summary.get("collection") or {}).get("by_mode") or {}
    labels = (summary.get("collection") or {}).get("labels") or {}
    items, _ = S._light_events(S._now())
    captured_times = sorted(i["prediction_as_of"] for i in items
                            if i.get("capture_status") == "WINDOW_CAPTURE" and i.get("prediction_as_of"))
    have_records = summary.get("artifacts", {}).get("capture_records") == "present"
    period = (_period("capture period", captured_times[0], captured_times[-1],
                      "earliest and latest prediction_as_of among saved captures")
              if captured_times else None)

    def mode_count(m: str, key: str) -> int | None:
        return (by_mode.get(m) or {}).get(key) if have_records else None

    samples = [
        _sample("Captured events", counts.get("captured_events") if have_records else None,
                "accepted capture records on this machine", "no capture records on this machine"),
        _sample("Scored events", counts.get("scored_events") if have_records else None,
                "events with at least one finalized score", "no capture records on this machine"),
        _sample("Labelled contracts", counts.get("labelled_contracts") if have_records else None,
                "contracts with a Kalshi settlement label", "no capture records on this machine"),
    ]
    operations = _sports_operations()
    integ = summary.get("integrity") or {}
    results = [
        _result("Prospective results view", kind="api", available=bool(counts.get("scored_events")),
                href="/sports?tab=results", reason="no scored events on this machine"),
        _result("Coverage view", kind="api", available=True, href="/sports?tab=coverage"),
        _result("Pilot scores (tracked CSV)", kind="tracked",
                available=(DOCS / "sports_capture_v2_pilot_scores.csv").is_file(),
                path=DOCS / "sports_capture_v2_pilot_scores.csv"),
        _result("Prospective performance review (2026-10-21)", kind="tracked",
                available=(DOCS / "prospective_performance_review_2026-10-21.md").is_file(),
                path=DOCS / "prospective_performance_review_2026-10-21.md"),
        _result("Forecast-error diagnosis (2026-10-21)", kind="tracked",
                available=(DOCS / "sports_forecast_error_diagnosis_2026-10-21.md").is_file(),
                path=DOCS / "sports_forecast_error_diagnosis_2026-10-21.md"),
        _result("ITF singles challenger study v1", kind="tracked",
                available=(DOCS / "sports_itf_challenger_study_v1.md").is_file(),
                path=DOCS / "sports_itf_challenger_study_v1.md"),
        _result("ITF singles challenger capture v1", kind="tracked",
                available=(DOCS / "sports_itf_challenger_capture_v1.md").is_file(),
                path=DOCS / "sports_itf_challenger_capture_v1.md"),
    ]
    status = {
        "implementation": SR.implementation("Prospective capture and scoring",
                                            ["research/sports/live/capture.py", "research/sports/scoring/score.py"]),
        "evidence": [
            _protocol_evidence("Frozen protocol", proto_path),
            SR.record("evidence", "present" if have_records else "missing", "Capture records",
                      "accepted capture records found (checksums are checked when records are read)"
                      if have_records else "no accepted capture records on this machine",
                      source="data/sports/phase2/capture_v2"),
        ],
        "operations": operations,
        "integrity": _integrity_not_checked(False),
        "policy": _policy_records(proto, f"{pid}.json"),
    }
    card = {
        "lifecycle": {"value": "collecting" if operations["state"] == "recent_success" else "collection status unknown"
                      if operations["state"] == "unknown" else f"collector {operations['state'].replace('_', ' ')}",
                      "basis": operations["basis"]},
        "frozen_at": (proto or {}).get("frozen_at"),
        "period": period,
        "period_reason": None if period else "no saved captures on this machine",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card

    mismatches = (integ.get("record_checksum_mismatches") or 0) + (integ.get("score_checksum_mismatches") or 0)
    status["integrity"] = SR.record(
        "integrity",
        ("failed" if mismatches else "verified") if have_records else "not_checked",
        "Record checksums",
        "Per-record checksums verified when saved records and scores were read (cached by file size and mtime)",
        reason=(f"{mismatches} checksum mismatches" if mismatches else None) if have_records
        else "No capture records on this machine",
        as_of=summary.get("generated_at"),
    )
    status["integrity"]["checks"] = integ
    amendment = (summary.get("collection") or {}).get("amendment") or {}
    rules = (proto or {}).get("rules") or {}
    card.update({
        "question": CAPTURE_QUESTION,
        "reference": S.MARKET_NOTE,
        "candidates": _candidates_from(rules) or [],
        "cohort_rules": [{"name": k, "rule": str(v)} for k, v in rules.items() if k != "candidates"],
        "limitations": [
            S.PILOT_LABEL,
            "Captures need the machine awake, logged in and online during each event's window; missed "
            "windows are never back-filled.",
            "RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.",
        ],
        "mode_blocks": [
            _mode("historical_replay", False,
                  "Not part of this protocol. Historical results are Phase 1 and Phase 2 (sports.phase1_v2, "
                  "sports.phase2_v1)."),
            _mode("manual_pilot", True,
                  f"{labels.get('manual_pilot', 'Manual pilot capture')} and "
                  f"{labels.get('collector_manual', 'collector cycle started by hand').lower()}.",
                  [_sample("Manual pilot captures", mode_count("manual_pilot", "captured"),
                           "events by collection mode", "no capture records on this machine"),
                   _sample("Collector cycles started by hand", mode_count("collector_manual", "captured"),
                           "events by collection mode", "no capture records on this machine")],
                  None, href="/sports?tab=events&collection=manual_pilot"),
            _mode("automatic_collection", True,
                  f"{labels.get('automatic', 'Automatic (scheduled collector)')}, added by "
                  f"{amendment.get('id') or 'the operations amendment'} without modifying the protocol.",
                  [_sample("Automatic captures", mode_count("automatic", "captured"),
                           "events by collection mode", "no capture records on this machine"),
                   _sample("Automatic missed windows", mode_count("automatic", "missed"),
                           "MISSED records by collection mode", "no capture records on this machine")],
                  None, operations=operations, href="/sports?tab=events&collection=automatic"),
        ],
        "sources": [
            _source("Frozen protocol", proto_path, True),
            _source("Operations amendment", DOCS / "sports_phase2_capture_v2_ops_amendment_1.md", True),
            _source("Capture protocol notes", DOCS / "sports_capture_v2.md", True),
            _source("Scoring notes", DOCS / "sports_capture_v2_scoring.md", True),
            _source("Pilot scores", DOCS / "sports_capture_v2_pilot_scores.csv", True),
            _source("Prospective performance review (2026-10-21)",
                    DOCS / "prospective_performance_review_2026-10-21.md", True),
            _source("Forecast-error diagnosis (2026-10-21)",
                    DOCS / "sports_forecast_error_diagnosis_2026-10-21.md", True),
            _source("ITF singles challenger study v1",
                    DOCS / "sports_itf_challenger_study_v1.md", True),
            _source("ITF singles challenger capture v1",
                    DOCS / "sports_itf_challenger_capture_v1.md", True),
            _source("Capture records", LC.CAPTURE_DIR, False),
            _source("Finalized scores", SCC.SCORE_DIR, False),
        ],
        "frozen_settings": _compact(proto) if proto else None,
        "links": [
            {"label": "Prospective results", "href": "/sports?tab=results"},
            {"label": "Coverage", "href": "/sports?tab=coverage"},
            {"label": "Settled events", "href": "/sports?tab=events&status=settled"},
            {"label": "Automatic captures", "href": "/sports?tab=events&collection=automatic"},
        ],
    })
    return card


CAPTURE_QUESTION = ("Do the frozen sports candidates, captured prospectively before each event starts, keep their "
                    "historical accuracy against Kalshi settlement and the market reference?")
SPORTS_LOADING = ("The sports index is still being built after the API started; counts appear once it is "
                  "ready. Retry shortly.")


def _sports_index_ready(S: Any, LC: Any) -> bool:
    """False while saved sports records exist but have never been parsed (cold start).

    Parsing every record and score takes minutes on a cold start; catalog cards must not wait for it.
    """
    root = LC.CAPTURE_DIR / "accepted" / "checkpoint"
    first = next(root.glob("*/*.json"), None) if root.is_dir() else None
    return first is None or str(first) in S._records.files


def _sports_capture_loading(proto: dict | None, proto_path: Path, detail: bool) -> dict:
    pid = "sports_phase2_capture_v2"
    operations = _sports_operations()
    card = {
        "lifecycle": {"value": "counts loading", "basis": SPORTS_LOADING},
        "frozen_at": (proto or {}).get("frozen_at"),
        "period": None,
        "period_reason": SPORTS_LOADING,
        "samples": [_sample(label, None, SPORTS_LOADING) for label in
                    ("Captured events", "Scored events", "Labelled contracts")],
        "results": [_result("Prospective results view", kind="api", available=True, href="/sports?tab=results")],
        "status": {
            "implementation": SR.implementation("Prospective capture and scoring",
                                                ["research/sports/live/capture.py", "research/sports/scoring/score.py"]),
            "evidence": [_protocol_evidence("Frozen protocol", proto_path),
                         SR.not_checked("evidence", "Capture records", SPORTS_LOADING)],
            "operations": operations,
            "integrity": _integrity_not_checked(detail),
            "policy": _policy_records(proto, f"{pid}.json"),
        },
        "loading": True,
    }
    if detail:
        card.update({"question": CAPTURE_QUESTION, "reference": None, "candidates": [], "cohort_rules": [],
                     "limitations": [SPORTS_LOADING], "mode_blocks": [], "sources": [
                         _source("Frozen protocol", proto_path, True)],
                     "frozen_settings": _compact(proto) if proto else None,
                     "links": [{"label": "Prospective results", "href": "/sports?tab=results"}]})
    return card


def _mode(mode: str, applies: bool, description: str, samples: list[dict] | None = None,
          period: dict | None = None, operations: dict | None = None, href: str | None = None) -> dict:
    return {"mode": mode, "label": MODE_LABELS[mode], "applies": applies, "description": description,
            "samples": samples or [], "period": period, "operations": operations, "href": href}


# ---------------------------------------------------------------- weather

def _weather_operations() -> dict:
    from api import weather_index as WI

    now = datetime.now(timezone.utc)
    try:
        cycles = WI._cycles(now)
    except OSError:
        cycles = []
    source = "data/logs/prospective/*.log"
    if not cycles:
        return SR.unknown("operations", "Weather collector", "No prospective task log cycles on this machine",
                          source=source)
    last = cycles[-1]
    ok = [c for c in cycles if c["exit_code"] == 0]
    basis = f"prospective task log cycle end lines (stale after {2 * WEATHER_CADENCE_MIN + 5} min)"
    if last["exit_code"] != 0:
        return SR.record("operations", "failed", "Weather collector", basis, source=source,
                         as_of=last["finished_at"], reason=f"Last cycle exit code {last['exit_code']}")
    age_h = SR.age_hours(ok[-1]["finished_at"], now) if ok else None
    if age_h is None:
        return SR.unknown("operations", "Weather collector", "No successful cycle time recorded", source=source)
    state = "recent_success" if age_h * 60 <= 2 * WEATHER_CADENCE_MIN + 5 else "stale"
    return SR.record("operations", state, "Weather collector", basis, source=source, as_of=ok[-1]["finished_at"],
                     reason=None if state == "recent_success" else f"Last successful cycle {age_h * 60:.0f} min ago")


def _weather_clinyc(detail: bool) -> dict:
    hyp_path = WEATHER_HYPOTHESES / "clinyc_identity_transfer.json"
    hyp = _json_dict(hyp_path)
    art_path = RESULTS_ROOT / "settlement_transfer_assessment.json"
    art_raw = _json(art_path)
    art = art_raw if isinstance(art_raw, dict) else None
    pairs_csv = REPO_ROOT / "data" / "weather" / "calibration" / "knyc_clinyc_pairs.csv"
    samples = [
        _sample("Development pairs (pre-registration)", (hyp or {}).get("development_pair_count"),
                "development_pair_count in the tracked hypothesis"),
        _sample("Prospective paired events", (art or {}).get("prospective_n"),
                "prospective_n in settlement_transfer_assessment.json", "transfer assessment artifact not available"),
        _sample("Prospective target", (art or {}).get("prospective_target_n"),
                "prospective_target_n in settlement_transfer_assessment.json",
                "transfer assessment artifact not available"),
    ]
    transfer_status = (art or {}).get("transfer_status")
    results = [
        _result("Transfer assessment (local)", kind="local", available=art is not None, path=art_path,
                reason="artifact missing or unparseable"),
        _result("Weather model page", kind="api", available=True, href="/weather-model"),
    ]
    status = {
        "implementation": SR.implementation("Transfer assessment", ["research/weather/transfer.py"]),
        "evidence": [
            _protocol_evidence("Registered hypothesis", hyp_path),
            SR.json_evidence("Transfer assessment", art_path, parsed=art),
            SR.file_evidence("KNYC/CLINYC pairs", pairs_csv, what="tracked data"),
        ],
        "operations": SR.record("operations", "not_applicable", "Operations",
                                "Re-assessed from saved pairs; no separate collector",
                                reason="Re-assessed from saved pairs; no separate collector"),
        "integrity": _integrity_not_checked(False),
        "policy": [SR.policy("Mode", "RESEARCH_ONLY", "transfer is experimental until its frozen criteria pass")],
    }
    card = {
        "lifecycle": {"value": f"transfer {transfer_status}" if transfer_status else "transfer status unknown",
                      "basis": "transfer_status in settlement_transfer_assessment.json"
                      if transfer_status else "artifact missing or has no transfer_status"},
        "frozen_at": (hyp or {}).get("registered_at"),
        "period": _period("prospective validation", (hyp or {}).get("validation_start_date"), None,
                          "validation_start_date in the tracked hypothesis; open-ended")
        if (hyp or {}).get("validation_start_date") else None,
        "period_reason": None if (hyp or {}).get("validation_start_date") else "hypothesis has no validation start",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card
    status["integrity"] = _integrity_not_checked(True)
    criteria = (hyp or {}).get("criteria") or {}
    card.update({
        "question": "Can the KNYC NWS CLI daily high stand in for the CLINYC (Weather Company) settlement value "
                    "by identity, within the pre-registered mismatch criteria?",
        "reference": "Observed CLINYC settlement values for the same dates.",
        "candidates": [{"id": "identity", "label": "Identity transfer",
                        "description": f"{(hyp or {}).get('source_regime')} -> {(hyp or {}).get('target_regime')} "
                                       f"({(hyp or {}).get('transfer_rule')})"}],
        "cohort_rules": [{"name": k, "rule": str(v)} for k, v in criteria.items()],
        "limitations": [*((hyp or {}).get("notes") or []),
                        "Transfer stays experimental until the frozen criteria are met in the prospective window."],
        "mode_blocks": [
            _mode("historical_replay", True, "Development pairs motivated the hypothesis; they are not "
                  "confirmation evidence.", samples[:1]),
            _mode("manual_pilot", False, "Not part of this hypothesis."),
            _mode("automatic_collection", True, "Prospective pairs accrue as CLINYC settles after the validation "
                  "start date; the assessment artifact is rebuilt outside the web API.", samples[1:], card["period"]),
        ],
        "sources": [
            _source("Registered hypothesis", hyp_path, True),
            _source("KNYC/CLINYC pairs", pairs_csv, True),
            _source("Transfer assessment", art_path, False),
            _source("Difference report", RESULTS_ROOT / "knyc_clinyc_difference_report.json", False),
        ],
        "frozen_settings": _compact(hyp) if hyp else None,
        "links": [{"label": "Weather model", "href": "/weather-model"}, {"label": "Saved forecasts", "href": "/markets"}],
    })
    return card


def _weather_shadow(detail: bool) -> dict:
    hyp_path = WEATHER_HYPOTHESES / "shadow_gfs_hrrr_equal_v1.json"
    hyp = _json_dict(hyp_path)
    eval_path = RESULTS_ROOT / "shadow_model_evaluation.json"
    ev_raw = _json(eval_path)
    ev = ev_raw if isinstance(ev_raw, dict) else None
    cmp_path = RESULTS_ROOT / "operational_model_comparison.json"
    pv_path = RESULTS_ROOT / "prospective_validation_summary.json"
    pv = _json_dict(pv_path)
    scored = ((pv or {}).get("totals") or {}).get("scored_snapshots")
    samples = [
        _sample("Scored prospective snapshots", scored, "totals.scored_snapshots in prospective_validation_summary.json",
                "prospective validation summary not available"),
    ]
    results = [
        _result("Shadow evaluation (local)", kind="local", available=ev is not None, path=eval_path,
                reason="artifact missing or unparseable"),
        _result("Operational comparison (local)", kind="local", available=cmp_path.is_file(), path=cmp_path),
        _result("Research summary", kind="api", available=True, href="/research#weather-model-summary"),
    ]
    status = {
        "implementation": SR.implementation("Shadow evaluation", ["research/weather/shadow.py", "research/weather/phase5.py"]),
        "evidence": [
            _protocol_evidence("Registered candidate", hyp_path),
            SR.json_evidence("Shadow evaluation", eval_path, parsed=ev),
        ],
        "operations": SR.record("operations", "not_applicable", "Operations",
                                "Evaluated from saved snapshots; collection is reported under Phase 7",
                                reason="Evaluated from saved snapshots; collection is reported under Phase 7"),
        "integrity": _integrity_not_checked(False),
        "policy": _policy_records(hyp, "shadow_gfs_hrrr_equal_v1.json")
        + [SR.policy("Mode", "RESEARCH_ONLY", "shadow candidates never drive decisions")],
    }
    card = {
        "lifecycle": {"value": "shadow" if (hyp or {}).get("status") == "shadow" else "status unknown",
                      "basis": "status in the tracked candidate file"},
        "frozen_at": (hyp or {}).get("registered_at"),
        "period": None,
        "period_reason": "the candidate file does not define an evaluation period",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card
    status["integrity"] = _integrity_not_checked(True)
    card.update({
        "question": "Does an equal 50/50 blend of calibrated GFS and calibrated HRRR bucket probabilities have "
                    "lower Brier score than calibrated GFS alone?",
        "reference": f"Incumbent: {(hyp or {}).get('incumbent_model') or 'unknown'}.",
        "candidates": [{"id": (hyp or {}).get("candidate_id") or "SHADOW_GFS_HRRR_EQUAL_V1",
                        "label": "GFS/HRRR equal blend", "description": str((hyp or {}).get("definition"))}],
        "cohort_rules": [{"name": k, "rule": str(v)} for k, v in (hyp or {}).items()
                         if k.startswith("no_") or k in ("bucket_alignment", "required_sources")],
        "limitations": [
            "SHADOW ONLY: never promoted automatically and never used for decisions.",
            "A confidence interval excluding zero would not be validation.",
        ],
        "mode_blocks": [
            _mode("historical_replay", True, "Operational replay evaluation (Phase 5/6) on saved historical data."),
            _mode("manual_pilot", False, "Not part of this candidate."),
            _mode("automatic_collection", True, "Scored on snapshots saved before the outcome by the prospective "
                  "collector.", samples),
        ],
        "sources": [
            _source("Registered candidate", hyp_path, True),
            _source("Shadow evaluation", eval_path, False),
            _source("Operational comparison", cmp_path, False),
            _source("Prospective validation summary", pv_path, False),
        ],
        "frozen_settings": _compact(hyp) if hyp else None,
        "links": [{"label": "Saved forecasts", "href": "/markets"},
                  {"label": "Weather model", "href": "/weather-model"}],
    })
    return card


def _weather_phase6(detail: bool) -> dict:
    doc = DOCS / "phase6_v2_1_summary.md"
    replay = REPO_ROOT / "data" / "weather" / "calibration" / "gfs_operational_replay_obs_v2_1.csv"
    info = _csv_summary(replay, "target_date")
    shadow_eval = RESULTS_ROOT / "phase6_shadow_evaluation_v2_1.json"
    dates = (info or {}).get("dates")
    samples = [
        _sample("GFS replay rows (tracked CSV)", (info or {}).get("rows"),
                "rows in data/weather/calibration/gfs_operational_replay_obs_v2_1.csv", "replay CSV not found"),
        _sample("Target dates", dates[2] if dates else None, "distinct target_date values in the replay CSV",
                "replay CSV not found or has no dates"),
    ]
    results = [
        _result("Research summary", kind="tracked", available=doc.is_file(), path=doc),
        _result("Shadow evaluation v2_1 (local)", kind="local", available=shadow_eval.is_file(), path=shadow_eval),
    ]
    status = {
        "implementation": SR.implementation("Phase 6 replay", ["research/weather/phase6.py"]),
        "evidence": [
            SR.file_evidence("Research summary", doc, what="tracked summary"),
            SR.file_evidence("Replay CSV", replay, what="tracked data"),
            SR.json_evidence("Shadow evaluation v2_1", shadow_eval),
        ],
        "operations": SR.record("operations", "not_applicable", "Operations", "Historical replay; no ongoing collection",
                                reason="Historical replay; no ongoing collection"),
        "integrity": _integrity_not_checked(False),
        "policy": [SR.policy("Mode", "RESEARCH_ONLY", "phase6_v2_1_summary.md"),
                   SR.policy("Decision", "NO_BET", "phase6_v2_1_summary.md"),
                   SR.policy("Candidate", "SHADOW_ONLY", "phase6_v2_1_summary.md")],
    }
    card = {
        "lifecycle": {"value": "historical (v2_1 supersedes v2)", "basis": "phase6_v2_1_summary.md"},
        "frozen_at": None,
        "period": _period("replay target dates", dates[0], dates[1], "min and max target_date in the replay CSV")
        if dates else None,
        "period_reason": None if dates else "replay CSV not found",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card
    status["integrity"] = _integrity_not_checked(True)
    card.update({
        "question": "With KNYC as-of observations and operational run selection, how do calibrated GFS, "
                    "calibrated HRRR and their equal-weight shadow compare on historical checkpoints?",
        "reference": "Incumbent calibrated GFS.",
        "candidates": [{"id": "gfs", "label": "Calibrated GFS", "description": "Incumbent"},
                       {"id": "hrrr", "label": "Calibrated HRRR", "description": "Component"},
                       {"id": "shadow", "label": "SHADOW_GFS_HRRR_EQUAL_V1", "description": "0.5*P_gfs + 0.5*P_hrrr"}],
        "cohort_rules": [{"name": "common cohort", "rule": "(target_date, checkpoint) pairs where GFS and HRRR "
                          "version-C rows are both FULL_OPERATIONAL_REPLAY; see the summary for details."}],
        "limitations": ["Historical replay only; not a prospective result.",
                        "No frozen protocol file; the tracked summary documents the methodology.",
                        "A confidence interval excluding zero would not be validation."],
        "mode_blocks": [
            _mode("historical_replay", True, "Operational replay with as-of observations.", samples, card["period"]),
            _mode("manual_pilot", False, "Not part of this study."),
            _mode("automatic_collection", False, "Not part of this study. Prospective weather collection is "
                  "weather.phase7_prospective_v1."),
        ],
        "sources": [
            _source("Research summary", doc, True),
            _source("GFS replay CSV", replay, True),
            _source("HRRR replay CSV", REPO_ROOT / "data" / "weather" / "calibration" / "hrrr_operational_replay_obs_v2_1.csv", True),
            _source("Shadow evaluation v2_1", shadow_eval, False),
            _source("Methodology v2_1", RESULTS_ROOT / "phase6_methodology_v2_1.json", False),
        ],
        "frozen_settings": None,
        "links": [{"label": "Weather model", "href": "/weather-model"}],
    })
    return card


def _weather_phase7(detail: bool) -> dict:
    proto_path = WEATHER_HYPOTHESES / "phase7_prospective_protocol_v1.json"
    proto = _json_dict(proto_path)
    pools = PHASE7_ROOT / "frozen_pools_v1.csv"
    counts = {d: _file_count(PHASE7_ROOT / d) for d in ("receipts", "records", "scores")}
    samples = [
        _sample("Checkpoint receipts", counts["receipts"], "files in data/weather/phase7/receipts (not parsed)",
                "receipts directory not found"),
        _sample("Prediction records", counts["records"], "files in data/weather/phase7/records (not parsed)",
                "records directory not found"),
        _sample("Final scores", counts["scores"], "files in data/weather/phase7/scores (not parsed)",
                "scores directory not found"),
        _sample("Registered target dates", ((proto or {}).get("collection") or {}).get("n_target_dates"),
                "collection.n_target_dates in the protocol"),
    ]
    operations = _weather_operations()
    results = [
        _result("Saved forecasts", kind="api", available=True, href="/markets"),
        _result("Prediction records (local)", kind="local", available=bool(counts["records"]), path=PHASE7_ROOT / "records",
                reason="no prediction records on this machine"),
        _result("Prospective performance review (2026-10-21)", kind="tracked",
                available=(DOCS / "prospective_performance_review_2026-10-21.md").is_file(),
                path=DOCS / "prospective_performance_review_2026-10-21.md"),
    ]
    status = {
        "implementation": SR.implementation("Phase 7 protocol", ["research/weather/phase7.py",
                                                                 "research/weather/phase7_method.py"]),
        "evidence": [
            _protocol_evidence("Frozen protocol", proto_path),
            SR.file_evidence("Frozen calibration pool", pools, what="tracked data"),
            SR.record("evidence", "present" if counts["scores"] else "missing", "Final scores",
                      f"{counts['scores']} score files present; contents not parsed"
                      if counts["scores"] else "no score files on this machine",
                      source="data/weather/phase7/scores"),
        ],
        "operations": operations,
        "integrity": _integrity_not_checked(False),
        "policy": _policy_records(proto, "phase7_prospective_protocol_v1.json"),
    }
    card = {
        "lifecycle": {"value": "collecting" if operations["state"] == "recent_success"
                      else f"collector {operations['state'].replace('_', ' ')}",
                      "basis": operations["basis"]},
        "frozen_at": (proto or {}).get("registered_at"),
        "period": _period("registered target dates", (proto or {}).get("start_target_date"),
                          (proto or {}).get("end_target_date"), "start/end_target_date in the protocol")
        if proto else None,
        "period_reason": None if proto else "protocol file missing",
        "samples": samples,
        "results": results,
        "status": status,
    }
    if not detail:
        return card
    status["integrity"] = SR.not_checked(
        "integrity", "Method and pool pins",
        "Source-hash and frozen-pool verification is run by the collector before each capture, not by the web API",
    )
    pc = (proto or {}).get("primary_comparison") or {}
    cohort = (proto or {}).get("primary_cohort") or {}
    dists = (proto or {}).get("research_distributions") or {}
    card.update({
        "question": "Prospectively, does the GFS/HRRR equal-weight shadow have lower multiclass Brier score than "
                    "research GFS on registered KXHIGHNY target dates?",
        "reference": f"Contrast: {pc.get('contrast') or 'unknown'}; {pc.get('legacy_incumbent') or ''}".strip("; "),
        "candidates": [{"id": k, "label": k, "description": str(v)} for k, v in dists.items()
                       if k.startswith("research_")],
        "cohort_rules": [{"name": k, "rule": str(v)} for k, v in cohort.items()],
        "limitations": [
            f"Below {pc.get('minimum_paired_target_dates', 'the minimum')} paired target dates the result is "
            f"{pc.get('below_minimum_label', 'INSUFFICIENT')}; before the end it is descriptive interim only.",
            "Captures need the machine awake, logged in and online at each checkpoint; missed checkpoints are "
            "recorded and never reconstructed.",
            "SHADOW ONLY / NO_BET: no automatic promotion, prices, trades or weights.",
        ],
        "mode_blocks": [
            _mode("historical_replay", False, "Not part of this protocol. Historical replay is weather.phase6_v2_1."),
            _mode("manual_pilot", False, "Not part of this protocol."),
            _mode("automatic_collection", True, "Hourly prospective task captures each registered checkpoint "
                  "inside its window.", samples, card["period"], operations=operations, href="/markets"),
        ],
        "sources": [
            _source("Frozen protocol", proto_path, True),
            _source("Frozen calibration pool", pools, True),
            _source("Runbook", DOCS / "phase7_hosting_runbook.md", True),
            _source("Operational misses note", DOCS / "phase7_operational_misses_note.md", True),
            _source("Prospective performance review (2026-10-21)",
                    DOCS / "prospective_performance_review_2026-10-21.md", True),
            _source("Receipts", PHASE7_ROOT / "receipts", False),
            _source("Prediction records", PHASE7_ROOT / "records", False),
            _source("Final scores", PHASE7_ROOT / "scores", False),
        ],
        "frozen_settings": _compact(proto) if proto else None,
        "links": [{"label": "Saved forecasts", "href": "/markets"},
                  {"label": "Settled saved forecasts", "href": "/markets?status=settled"}],
    })
    return card


# ---------------------------------------------------------------- registry

REGISTRY: list[dict[str, Any]] = [
    {"id": "weather.phase7_prospective_v1", "category": "weather", "protocol_id": "PHASE7_PROSPECTIVE_V1",
     "title": "Weather Phase 7: prospective GFS/HRRR shadow validation", "version": "v1",
     "designation": "prospective", "modes": ["automatic_collection"], "build": _weather_phase7},
    {"id": "weather.clinyc_transfer_v1", "category": "weather", "protocol_id": "CLINYC_TRANSFER_V1",
     "title": "Weather: KNYC to CLINYC settlement transfer", "version": "v1",
     "designation": "prospective", "modes": ["historical_replay", "automatic_collection"], "build": _weather_clinyc},
    {"id": "weather.shadow_gfs_hrrr_equal_v1", "category": "weather", "protocol_id": "SHADOW_GFS_HRRR_EQUAL_V1",
     "title": "Weather shadow candidate: GFS/HRRR equal blend", "version": "v1",
     "designation": "historical", "modes": ["historical_replay", "automatic_collection"], "build": _weather_shadow},
    {"id": "weather.phase6_v2_1", "category": "weather", "protocol_id": None,
     "title": "Weather Phase 6: as-of observations and operational replay", "version": "v2_1",
     "designation": "historical", "modes": ["historical_replay"], "build": _weather_phase6},
    {"id": "sports.phase2_capture_v2", "category": "sports", "protocol_id": "sports_phase2_capture_v2",
     "title": "Sports prospective capture (Phase 2 capture v2)", "version": "v2",
     "designation": "prospective", "modes": ["manual_pilot", "automatic_collection"],
     "build": lambda detail: _sports_capture(detail)},
    *[
        {"id": eid, "category": "sports", "protocol_id": spec["protocol"], "title": spec["title"],
         "version": spec["version"], "designation": "historical", "modes": ["historical_replay"],
         "build": (lambda e: (lambda detail: _sports_historical(e, detail)))(eid)}
        for eid, spec in _SPORTS_HISTORICAL.items()
    ],
]
_BY_ID = {e["id"]: e for e in REGISTRY}


def _card(entry: dict, detail: bool) -> dict:
    base = {k: entry[k] for k in ("id", "category", "protocol_id", "title", "version", "designation", "modes")}
    base["summary_note"] = SUMMARY_NOTE
    try:
        dynamic = entry["build"](detail)
        base["error"] = None
    except Exception as exc:  # noqa: BLE001 - one experiment must never break the catalog
        dynamic = {
            "lifecycle": {"value": "unknown", "basis": "status could not be read"},
            "frozen_at": None, "period": None, "period_reason": "status could not be read",
            "samples": [], "results": [],
            "status": {"implementation": SR.unknown("implementation", "Implementation", "status could not be read"),
                       "evidence": [], "operations": SR.unknown("operations", "Operations", "status could not be read"),
                       "integrity": SR.not_checked("integrity", "Integrity", "status could not be read"),
                       "policy": []},
        }
        base["error"] = f"{type(exc).__name__}: {exc}"
    base.update(dynamic)
    # Links to app views are always reachable and do not count as results on their own.
    base["results_available"] = any(r["available"] for r in base.get("results", []) if r["kind"] != "api")
    return base


def list_experiments(category: str) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "mode": "RESEARCH_ONLY",
        "summary_note": SUMMARY_NOTE,
        "experiments": [_card(e, False) for e in REGISTRY if e["category"] == category],
    }


def get_experiment(experiment_id: str) -> dict | None:
    entry = _BY_ID.get(experiment_id)
    if entry is None:
        return None
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "RESEARCH_ONLY",
            **_card(entry, True)}


def tracked_paths() -> list[str]:
    """Every source declared as tracked, for preservation tests."""
    out = []
    for e in REGISTRY:
        card = _card(e, True)
        out += [s["path"] for s in card.get("sources", []) if s["tracked"]]
    return out
