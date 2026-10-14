"""Read-only sports adapter for the HTTP API (RESEARCH_ONLY / NO_BET).

Reads saved artifacts only: accepted ``sports_phase2_capture_v2`` checkpoint records, finalized
``sports_capture_v2_scoring_v1`` scores and settlement run summaries, the saved raw evidence of those
runs (for readable titles), tracked coverage / historical summaries under ``docs/research`` and the
collector status files. Nothing here fetches upstream data, captures, settles, writes reports or changes
research state. Missing artifacts and integrity problems are reported, never replaced by other data.

Responses never contain filesystem paths, credentials or complete evidence payloads.
"""

from __future__ import annotations

import csv
import functools
import json
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import evaluate as EV
from .adapters import KALSHI_BASE
from .core import PROTOCOLS, REPO_ROOT, request_key, sha256_bytes, sha256_file, sha256_json
from .live import config as LC
from .scoring import config as SCC

CANDIDATES = ["frozen", "calibrated", "market", "blend"]
FAMILY_ORDER = ["game_winner", "spread", "total", "team_total", "segment"]
DOCS = REPO_ROOT / "docs" / "research"
SERIES_MATRIX_CSV = DOCS / "sports_capture_v2_series_matrix.csv"
COMP_MATRIX_CSV = DOCS / "sports_capture_v2_matrix.csv"
INVENTORY_CSV = DOCS / "sports_inventory_v1.csv"
RESULTS_ROOT = REPO_ROOT / "data" / "results"
VIEWS = ("upcoming", "awaiting_settlement", "settled", "missed")
VIEW_ORDER = {v: i for i, v in enumerate(VIEWS)}
SOURCE_SAVED = "saved captures (local artifacts)"
PILOT_LABEL = ("Prospective pilot: manual and scheduled availability-based captures. Descriptive only; not a "
               "representative sample and not an independent confirmation of historical results.")
SIGN_NOTE = ("Differences are candidate minus reference on the same contracts. A negative value means the candidate "
             "had lower error (Brier or log loss) than the reference on those contracts.")
BINARY_NOTE = ("Each contract is a separate binary question. Spread and total strikes overlap (a team covering -7.5 "
               "also covers -3.5), so probabilities across strikes are not mutually exclusive buckets and do not "
               "sum to one.")
MARKET_NOTE = ("The market reference is the bid/ask midpoint of the latest hourly Kalshi candle that ended at or "
               "before prediction_as_of (frozen rule: two-sided book, candle age at most 3 h). It is a historical "
               "reference price, not an executable quote. Current bid/ask from the market listing are shown "
               "separately as diagnostics and are never a candidate or blend input.")

HISTORICAL = [
    {"protocol": "sports_phase1_v1", "phase": "Phase 1", "results_csv": "sports_phase1_results_v1.csv",
     "report": "sports_phase1_results_v1.md", "results_dir": "sports_phase1",
     "comparison": "frozen model vs naive baseline (retrospective)"},
    {"protocol": "sports_phase1_v2", "phase": "Phase 1", "results_csv": "sports_phase1_results_v2.csv",
     "report": "sports_phase1_results_v2.md", "results_dir": "sports_phase1",
     "comparison": "frozen model vs naive baseline (retrospective correction)"},
    {"protocol": "sports_phase2_v1", "phase": "Phase 2", "results_csv": "sports_phase2_results_v1.csv",
     "report": "sports_phase2_results_v1.md", "results_dir": "sports_phase2",
     "comparison": "paired candidate comparisons (frozen, calibrated, market, blend; historical folds)",
     "addenda": ["sports_phase2_results_v1_addendum1.md"]},
]

_lock = threading.Lock()
_active = [0]  # requests in progress; the background warm-up waits while this is non-zero
_active_lock = threading.Lock()


def _request(fn):
    """Mark a public entry point as a request in progress (see :func:`warm`)."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with _active_lock:
            _active[0] += 1
        try:
            return fn(*args, **kwargs)
        finally:
            with _active_lock:
                _active[0] -= 1
    return run


def _now() -> float:
    return time.time()


def _iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def event_id(competition: str, cluster: str) -> str:
    """Stable event identity: the scoring event key ``competition|cluster`` (one event, many markets)."""
    return f"{competition}|{cluster}"


@lru_cache(maxsize=1)
def _comp_info() -> dict:
    from .competitions import all_competitions
    comps, _, _ = all_competitions()
    return {c.key: {"sport": c.sport, "source": c.source, "name": c.name} for c in comps}


# ---------------------------------------------------------------- cached file scans (write-once artifacts)

class _Cache:
    """Parsed file contents, reused only while the file's (mtime_ns, size) is unchanged."""

    def __init__(self):
        self.files: dict[str, tuple[int, int, object]] = {}

    def load(self, p: Path, parse):
        try:
            st = p.stat()
        except OSError:
            return None
        return self.get(p, st.st_mtime_ns, st.st_size, parse)

    def get(self, p: Path, mtime_ns: int, size: int, parse):
        key = str(p)
        hit = self.files.get(key)
        if hit and hit[0] == mtime_ns and hit[1] == size:
            return hit[2]
        val = parse(p)
        self.files[key] = (mtime_ns, size, val)
        return val


def _scan_json(root: Path, depth: int) -> list[tuple[Path, int, int]]:
    """``*.json`` files exactly ``depth`` directories below ``root`` as (path, mtime_ns, size), in
    ``sorted(root.glob(...))`` order. One directory listing per folder; on Windows the listing already
    carries size and modification time, so no per-file stat call is needed."""
    out: list[tuple[Path, int, int]] = []

    def walk(d: str, level: int) -> None:
        try:
            it = os.scandir(d)
        except OSError:
            return
        with it:
            for e in it:
                try:
                    if level < depth:
                        if e.is_dir():
                            walk(e.path, level + 1)
                    elif e.name.lower().endswith(".json") and e.is_file():
                        st = e.stat()
                        out.append((Path(e.path), st.st_mtime_ns, st.st_size))
                except OSError:
                    continue

    walk(str(root), 0)
    out.sort(key=lambda x: x[0])
    return out


def _stat_sig(p: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _SigCache:
    """Derived values keyed by name, reused only while every file read to build them still has the
    (mtime_ns, size) it had when it was read (``None`` = absent). A changed, added or removed file
    rebuilds the value, so integrity checks run again on changed evidence."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, tuple[list[tuple[Path, tuple | None]], object]] = {}

    def get(self, key: str, build):
        with self.lock:
            hit = self.items.get(key)
            if hit and all(_stat_sig(p) == sig for p, sig in hit[0]):
                return hit[1]
            touched: list[tuple[Path, tuple | None]] = []
            val = build(touched)
            self.items[key] = (touched, val)
            return val

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


_records = _Cache()
_scores = _Cache()
_misc = _Cache()


def _summarize_record(p: Path) -> dict | None:
    rec = _read_json(p)
    if not isinstance(rec, dict):
        return {"unreadable": True, "file": p.name}
    comp = rec.get("competition")
    info = _comp_info().get(comp, {})
    ok = rec.get("record_sha256") == sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    contracts = []
    for c in rec.get("contracts") or []:
        cands = c.get("candidates") or {}
        contracts.append({"ticker": c["ticker"], "family": c.get("family"), "cohort": c.get("cohort"),
                          "status": c.get("status"), "reason": c.get("reason"),
                          "p": {k: ("p" in (cands.get(k) or {})) for k in CANDIDATES},
                          "why": {k: (cands.get(k) or {}).get("unavailable") for k in CANDIDATES}})
    return {"event_id": event_id(comp, rec.get("cluster")), "competition": comp, "cluster": rec.get("cluster"),
            "sport": rec.get("sport") or info.get("sport"), "source": rec.get("source") or info.get("source"),
            "status": rec.get("status"), "start": rec.get("start"), "scheduled_cutoff": rec.get("scheduled_cutoff"),
            "window_open": rec.get("window_open"), "prediction_as_of": rec.get("prediction_as_of"),
            "finalized_at": rec.get("finalized_at"), "written_at": rec.get("written_at") or rec.get("finalized_at"),
            "horizon_minutes": rec.get("horizon_minutes"), "run_id": rec.get("run_id"),
            "kalshi_event_tickers": rec.get("kalshi_event_tickers") or [], "comparability": rec.get("comparability"),
            "reason": rec.get("reason"), "checksum_ok": ok, "contracts": contracts, "_path": p}


def records(capture_dir: Path | None = None) -> list[dict]:
    root = (capture_dir or LC.CAPTURE_DIR) / "accepted" / "checkpoint"
    out = []
    with _lock:
        for p, mtime, size in _scan_json(root, 1):
            s = _records.get(p, mtime, size, _summarize_record)
            if s:
                out.append(s)
    return out


def _load_score(p: Path) -> dict | None:
    sc = _read_json(p)
    if not isinstance(sc, dict):
        return None
    ok = sc.get("score_sha256") == sha256_json({k: v for k, v in sc.items() if k != "score_sha256"})
    sc = {k: v for k, v in sc.items() if k not in ("settlement_evidence", "source_record")}
    sc["checksum_ok"] = ok
    return sc


def scores(score_dir: Path | None = None) -> list[dict]:
    root = (score_dir or SCC.SCORE_DIR) / "finalized"
    out = []
    with _lock:
        for p, mtime, size in _scan_json(root, 2):
            s = _scores.get(p, mtime, size, _load_score)
            if s:
                out.append(s)
    return out


def _latest_json(directory: Path) -> dict | None:
    runs = sorted(directory.glob("*.json")) if directory.exists() else []
    return _misc.load(runs[-1], _read_json) if runs else None


def latest_settlement_run(score_dir: Path | None = None) -> dict | None:
    return _latest_json((score_dir or SCC.SCORE_DIR) / "runs")


def latest_capture_run(capture_dir: Path | None = None) -> dict | None:
    return _latest_json((capture_dir or LC.CAPTURE_DIR) / "runs")


# ---------------------------------------------------------------- titles from saved evidence (read-only)

def _evidence_body(run_id: str, source: str, key: str, sha: str | None,
                   touched: list | None = None) -> tuple[bytes | None, str]:
    """Saved evidence body, verified against its SHA-256. ``touched`` collects (path, signature) of each
    file consulted, taken before reading, for :class:`_SigCache`."""
    p = LC.EVIDENCE_ROOT / run_id / source / key[:2] / f"{key}.body"
    sig = _stat_sig(p)
    if touched is not None:
        touched.append((p, sig))
    if sig is None:
        return None, "missing"
    body = p.read_bytes()
    if sha is None:
        mp = p.with_name(f"{key}.meta.json")
        if touched is not None:
            touched.append((mp, _stat_sig(mp)))
        meta = _read_json(mp) or {}
        sha = meta.get("sha256")
    if sha is None or sha256_bytes(body) != sha:
        return None, "hash_mismatch"
    return body, "ok"


_titles = _SigCache()
_descriptions = _SigCache()


def clear_caches() -> None:
    _titles.clear()
    _descriptions.clear()


def _milestone_titles(run_id: str) -> dict:
    return _titles.get(f"{LC.EVIDENCE_ROOT}::{run_id}", lambda touched: _build_milestone_titles(run_id, touched))


def _build_milestone_titles(run_id: str, touched: list) -> dict:
    """event_ticker -> milestone title/start, walking the run's saved milestone pages by cursor."""
    url = f"{KALSHI_BASE}/milestones"
    params = {"limit": LC.PAGE_LIMITS["milestones"], "category": "Sports",
              "minimum_start_date": LC.MILESTONE_MIN_START}
    out = {}
    for _ in range(LC.MAX_PAGES):
        body, state = _evidence_body(run_id, "kalshi", request_key(url, params), None, touched)
        if body is None:
            break
        page = json.loads(body.decode("utf-8"))
        for m in page.get("milestones") or []:
            info = {"title": m.get("title"), "milestone_start": m.get("start_date"),
                    "league": (m.get("details") or {}).get("league")}
            for t in set((m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or [])):
                out.setdefault(t, info)
        cursor = page.get("cursor")
        if not cursor or not page.get("milestones"):
            break
        params = {**params, "cursor": cursor}
    return out


def _title_runs() -> list[str]:
    runs = sorted(p.stem for p in (LC.CAPTURE_DIR / "runs").glob("*.json")) if (LC.CAPTURE_DIR / "runs").exists() else []
    return list(reversed(runs))


def event_title(s: dict, title_runs: list[str] | None = None) -> dict:
    runs = (_title_runs() if title_runs is None else title_runs)[:3]
    for rid in [s.get("run_id")] + runs:
        if not rid:
            continue
        idx = _milestone_titles(rid)
        for t in s["kalshi_event_tickers"]:
            if t in idx:
                return {**idx[t], "title_source": "Kalshi milestone (saved evidence)"}
    return {"title": None, "title_source": "unavailable: saved milestone evidence not present locally"}


def _market_descriptions(record_key: str) -> tuple[dict, dict]:
    return _descriptions.get(record_key, lambda touched: _build_market_descriptions(record_key, touched))


def _build_market_descriptions(record_key: str, touched: list) -> tuple[dict, dict]:
    path, run_id = record_key.split("::", 1)
    touched.append((Path(path), _stat_sig(Path(path))))
    rec = _read_json(Path(path)) or {}
    own = set(rec.get("kalshi_event_tickers") or [])
    out, states = {}, Counter()
    for e in rec.get("evidence") or []:
        if not e.get("url", "").endswith("/markets") or (e.get("params") or {}).get("event_ticker") not in own:
            continue
        body, state = _evidence_body(run_id, e["source"], e["key"], e.get("sha256"), touched)
        states[state] += 1
        if body is None:
            continue
        for m in json.loads(body.decode("utf-8")).get("markets") or []:
            out[m["ticker"]] = {"title": m.get("title"), "yes_sub_title": m.get("yes_sub_title"),
                                "rules_primary": m.get("rules_primary")}
    return out, dict(states)


def _participants(s: dict, desc: dict, title: str | None) -> list[str]:
    names = sorted({(desc.get(c["ticker"]) or {}).get("yes_sub_title") for c in s["contracts"]
                    if c["family"] == "game_winner"} - {None})
    if names:
        return names
    for sep in (" at ", " vs. ", " vs "):
        if title and sep in title:
            return [x.strip() for x in title.split(sep, 1)]
    return []


# ---------------------------------------------------------------- events

def _score_index(all_scores: list[dict]) -> dict[str, dict[str, dict]]:
    by = defaultdict(dict)
    for sc in all_scores:
        by[sc["event_key"]][sc["ticker"]] = sc
    return by


def _settlement(s: dict, ev_scores: dict, pending_states: dict) -> dict:
    ok = [c["ticker"] for c in s["contracts"] if c["status"] == "ok"]
    final = [t for t in ok if t in ev_scores]
    states = Counter(ev_scores[t]["settlement"]["state"] for t in final)
    for t in ok:
        if t not in ev_scores:
            states[(pending_states.get(t) or {}).get("state", "NOT_YET_CHECKED")] += 1
    labelled = sum(1 for t in final if ev_scores[t].get("y") is not None)
    last_settled = max((_settled_at(ev_scores[t]) for t in final if _settled_at(ev_scores[t])), default=None)
    if not ok:
        state = "no_scorable_contracts"
    elif len(final) == len(ok):
        state = "settled"
    elif final:
        state = "partially_settled"
    else:
        state = "pending"
    return {"state": state, "contracts": len(ok), "final": len(final), "labelled": labelled,
            "pending": len(ok) - len(final), "states": dict(states), "last_settled_at": last_settled}


def _settled_at(sc: dict) -> str | None:
    """Kalshi settlement time recorded in the finalized score (None when the score does not record it)."""
    return ((sc.get("settlement") or {}).get("market") or {}).get("settlement_ts")


def _view(s: dict, settle: dict, now: float) -> str:
    if s["status"] == "MISSED":
        return "missed"
    if (_ts(s["start"]) or 0) > now:
        return "upcoming"
    return "settled" if settle["state"] in ("settled",) else "awaiting_settlement"


def _collection_modes() -> dict:
    from .ops import provenance as PV
    return PV.run_modes()


def _event_item(s: dict, ev_scores: dict, pending: dict, now: float, modes: dict | None = None,
                title_runs: list[str] | None = None) -> dict:
    return _enrich(_light_item(s, ev_scores, pending, now, modes), s, title_runs)


def _enrich(item: dict, s: dict, title_runs: list[str] | None = None) -> dict:
    """Fill the evidence-derived title and participants of a light item (in place)."""
    ttl = event_title(s, title_runs)
    desc = {}
    if s["status"] == "WINDOW_CAPTURE":
        desc, _ = _market_descriptions(f"{s['_path']}::{s['run_id']}")
    item["title"], item["title_source"] = ttl.get("title"), ttl.get("title_source")
    item["participants"] = _participants(s, desc, ttl.get("title"))
    return item


def _light_item(s: dict, ev_scores: dict, pending: dict, now: float, modes: dict | None = None) -> dict:
    """Everything except title and participants, which need saved evidence (see :func:`_enrich`)."""
    from .ops import provenance as PV
    settle = _settlement(s, ev_scores, pending) if s["status"] == "WINDOW_CAPTURE" else {
        "state": "not_applicable", "contracts": 0, "final": 0, "labelled": 0, "pending": 0, "states": {}}
    ok = [c for c in s["contracts"] if c["status"] == "ok"]
    return {
        "event_id": s["event_id"], "competition": s["competition"],
        "competition_name": _comp_info().get(s["competition"], {}).get("name"), "sport": s["sport"],
        "source": s["source"],
        "cluster": s["cluster"], "title": None, "title_source": None,
        "participants": [], "kalshi_event_tickers": s["kalshi_event_tickers"],
        "event_start": s["start"], "window_open": s["window_open"], "scheduled_cutoff": s["scheduled_cutoff"],
        "capture_status": s["status"], "prediction_as_of": s["prediction_as_of"], "finalized_at": s["finalized_at"],
        "horizon_minutes": s["horizon_minutes"], "missed_reason": s["reason"] if s["status"] == "MISSED" else None,
        "view": _view(s, settle, now), "settlement": settle,
        "families": sorted({c["family"] for c in ok if c["family"]}),
        "cohorts": sorted({c["cohort"] for c in ok if c["cohort"]}),
        "contracts_total": len(s["contracts"]), "contracts_ok": len(ok),
        "candidate_available": {k: sum(1 for c in ok if c["p"][k]) for k in CANDIDATES},
        "comparability": s["comparability"], "integrity": {"record_checksum": "ok" if s["checksum_ok"] else "MISMATCH"},
        "collection_mode": PV.mode_of(s.get("run_id"), modes if modes is not None else _collection_modes()),
    }


def _matches(item: dict, f: dict) -> bool:
    if f.get("sport") and item["sport"] != f["sport"]:
        return False
    if f.get("competition") and item["competition"] != f["competition"]:
        return False
    if f.get("family") and f["family"] not in item["families"]:
        return False
    if f.get("cohort") and f["cohort"] not in item["cohorts"]:
        return False
    if f.get("collection") and item.get("collection_mode") != f["collection"]:
        return False
    st = f.get("status")
    if st and st != "all":
        if st == "captured" and item["capture_status"] != "WINDOW_CAPTURE":
            return False
        if st in VIEWS and item["view"] != st:
            return False
    q = (f.get("q") or "").strip().lower()
    if q:
        hay = " ".join([item["event_id"], item.get("title") or "", item["competition"], item["sport"] or "",
                        " ".join(item["participants"]), " ".join(item["kalshi_event_tickers"])]).lower()
        if q not in hay:
            return False
    return True


def _sort_key(item: dict):
    start = _ts(item["event_start"]) or 0
    return (VIEW_ORDER[item["view"]], start if item["view"] == "upcoming" else -start, item["event_id"])


def _light_events(now: float, recs: list[dict] | None = None, all_scores: list[dict] | None = None
                  ) -> tuple[list[dict], dict[int, dict]]:
    """Sorted light items for every readable record, plus id(item) -> record summary for enrichment."""
    recs = records() if recs is None else recs
    idx = _score_index(scores() if all_scores is None else all_scores)
    run = latest_settlement_run() or {}
    pending = {d["ticker"]: d for d in run.get("diagnostics") or []}
    modes = _collection_modes()
    out, src = [], {}
    for s in recs:
        if "unreadable" in s:
            continue
        item = _light_item(s, idx.get(s["event_id"], {}), pending, now, modes)
        src[id(item)] = s
        out.append(item)
    return sorted(out, key=_sort_key), src


def all_events(now: float | None = None) -> list[dict]:
    now = now if now is not None else _now()
    items, src = _light_events(now)
    runs = _title_runs()
    return [_enrich(i, src[id(i)], runs) for i in items]


SORTS = ("default", "start_asc", "start_desc", "capture_desc", "settlement_desc")


def _sorted(items: list[dict], sort: str) -> list[dict]:
    """Presentation order only. Items missing the sort timestamp go last."""
    field = {"start_asc": "event_start", "start_desc": "event_start", "capture_desc": "prediction_as_of",
             "settlement_desc": None}.get(sort)
    if sort not in SORTS or sort == "default":
        return items

    def value(i):
        return i["settlement"].get("last_settled_at") if field is None else i[field]

    have = [i for i in items if value(i)]
    missing = [i for i in items if not value(i)]
    have.sort(key=lambda i: (_ts(value(i)), i["event_id"]), reverse=sort != "start_asc")
    return have + missing


@_request
def list_events(filters: dict, page: int = 1, page_size: int = 24, sort: str = "default") -> dict:
    loaded = _now()
    items, src = _light_events(loaded)
    facets = {k: sorted({str(x) for i in items for x in (i[k] if isinstance(i[k], list) else [i[k]]) if x})
              for k in ("sport", "competition", "families", "cohorts")}
    views = Counter(i["view"] for i in items)
    runs = _title_runs()
    if (filters.get("q") or "").strip():
        items = [_enrich(i, src[id(i)], runs) for i in items]  # search covers titles and participants
    sel = _sorted([i for i in items if _matches(i, filters)], sort)
    page_size = max(1, min(page_size, 200))
    pages = max(1, -(-len(sel) // page_size))
    page = max(1, min(page, pages))
    visible = sel[(page - 1) * page_size: page * page_size]
    for i in visible:
        if i["title_source"] is None:
            _enrich(i, src[id(i)], runs)
    return {"generated_at": _iso(loaded), "label": "Saved captures", "source": SOURCE_SAVED,
            "artifacts": artifacts(), "filters": {k: v for k, v in filters.items() if v},
            "sort": sort if sort in SORTS else "default", "total": len(sel),
            "page": page, "page_size": page_size, "pages": pages,
            "views": {v: views.get(v, 0) for v in VIEWS}, "facets": facets,
            "facet_labels": {"competition": {c: _comp_info().get(c, {}).get("name") or c for c in facets["competition"]}},
            "items": visible}


# ---------------------------------------------------------------- event detail

def _candidate_view(cand: dict, name: str, y: int | None) -> dict:
    if "p" not in cand:
        return {"available": False, "reason": cand.get("unavailable") or "unavailable (no reason recorded)"}
    out = {"available": True, "p": cand["p"]}
    if name == "frozen":
        out["model"] = cand.get("model")
    if name in ("calibrated", "blend"):
        out["map"] = cand.get("map")
    if name == "market":
        end = cand.get("candle_end")
        out["market_reference"] = {"mid": cand["p"], "bid": cand.get("bid"), "ask": cand.get("ask"),
                                   "candle_end_utc": _iso(end) if end is not None else None,
                                   "candle_age_s_at_prediction": cand.get("age_s")}
    if y is not None:
        b, ll = EV._losses({"p": cand["p"], "y": y})
        out["brier"], out["log_loss"] = b, ll
    return out


def _verify_evidence(rec: dict) -> dict:
    states = Counter()
    for e in rec.get("evidence") or []:
        if request_key(e["url"], e["params"]) != e["key"]:
            states["key_mismatch"] += 1
            continue
        _, st = _evidence_body(rec["run_id"], e["source"], e["key"], e["sha256"])
        states[st] += 1
    n = sum(states.values())
    status = ("not_applicable" if n == 0 else "verified" if states.get("ok") == n else
              "absent_locally" if states.get("missing") == n else "PROBLEM")
    return {"status": status, "files": dict(states)}


@lru_cache(maxsize=1)
def _protocol_info() -> dict:
    p = PROTOCOLS / f"{LC.PROTOCOL}.json"
    if not p.exists():
        return {"sha256": None, "pins": {}, "dependencies": {}}
    proto = json.loads(p.read_text(encoding="utf-8"))
    return {"sha256": sha256_file(p), "pins": proto.get("pins", {}), "dependencies": proto.get("dependencies", {}),
            "frozen_at": proto.get("frozen_at")}


def _dep_label(path: str) -> str:
    return path.replace("data/sports/", "").replace("docs/research/", "")


def _collection_info(run_id: str | None) -> dict:
    from .ops import provenance as PV
    modes = _collection_modes()
    mode = PV.mode_of(run_id, modes)
    return {"mode": mode, "label": PV.MODE_LABELS[mode],
            "cycle_started_at": (modes.get(run_id) or {}).get("cycle_started_at"),
            "amendment": PV.AMENDMENT["id"], "amendment_doc": PV.AMENDMENT["doc"],
            "note": "Derived from the collector cycle log by run_id; the record itself is unchanged."}


def _provenance(rec: dict, s: dict, ev_scores: dict) -> dict:
    proto = _protocol_info()
    ev = rec.get("evidence") or []
    as_of = rec.get("prediction_as_of_ts")
    timing_ok = None
    if rec.get("status") == "WINDOW_CAPTURE":
        timing_ok = (_ts(rec["window_open"]) <= as_of <= _ts(rec["finalized_at"]) <= _ts(rec["scheduled_cutoff"]))
    deps = rec.get("dependencies") or {}
    frozen_deps = proto["dependencies"].get(rec.get("competition"), {})
    return {
        "protocol": rec.get("protocol"), "schema": rec.get("schema"), "run_id": rec.get("run_id"),
        "capture_started_at": rec.get("capture_started_at"), "comparability_note": rec.get("comparability_note"),
        "timing_label": rec.get("timing_label"),
        "integrity": {
            "record_sha256": rec.get("record_sha256"), "record_checksum": "ok" if s["checksum_ok"] else "MISMATCH",
            "capture_protocol_sha256": rec.get("capture_protocol_sha256"),
            "capture_protocol_matches_current": rec.get("capture_protocol_sha256") == proto["sha256"],
            "pins_match_frozen_protocol": (rec.get("pins") == proto["pins"]) if rec.get("pins") else None,
            "dependencies_match_frozen_protocol": (deps == frozen_deps) if deps else None,
            "evidence": _verify_evidence(rec),
            "scores": {"count": len(ev_scores), "checksum_problems": sum(1 for x in ev_scores.values()
                                                                         if not x["checksum_ok"])},
            "note": "Full offline reproduction: python -m research.sports.live.run replay --all (not run by the API)",
        },
        "collection": _collection_info(rec.get("run_id")),
        "pins": rec.get("pins") or {},
        "dependencies": {_dep_label(k): v for k, v in deps.items()},
        "timing": {"window_open": rec.get("window_open"), "market_pick_time": rec.get("market_pick_time"),
                   "prediction_as_of": rec.get("prediction_as_of"), "finalized_at": rec.get("finalized_at"),
                   "scheduled_cutoff": rec.get("scheduled_cutoff"), "window_rule_satisfied": timing_ok,
                   "rule": "window_open <= prediction_as_of <= finalized_at <= scheduled_cutoff"},
        "evidence_timing": {
            "requests": len(ev), "bytes": sum(e.get("bytes") or 0 for e in ev),
            "by_source": dict(Counter(e["source"] for e in ev)),
            "first_requested_at": min((e["requested_at"] for e in ev), default=None),
            "last_received_at": max((e["received_at"] for e in ev), default=None),
            "all_received_before_prediction_as_of": (all(_ts(e["received_at"]) <= as_of for e in ev)
                                                     if ev and as_of else None),
            "non_200": sum(1 for e in ev if e.get("status") != 200)},
        "model": {k: (rec.get("model") or {}).get(k) for k in ("phase1_protocol", "primary_rule")},
        "settlement_runs": sorted({x.get("settlement_run_id") for x in ev_scores.values()} - {None}),
    }


@_request
def get_event(eid: str) -> dict | None:
    now = _now()
    s = next((x for x in records() if x.get("event_id") == eid), None)
    if s is None:
        return None
    idx = _score_index(scores())
    ev_scores = idx.get(eid, {})
    run = latest_settlement_run() or {}
    pending = {d["ticker"]: d for d in run.get("diagnostics") or []}
    item = _event_item(s, ev_scores, pending, now)
    rec = _read_json(s["_path"]) or {}
    desc, desc_states = ({}, {})
    if s["status"] == "WINDOW_CAPTURE":
        desc, desc_states = _market_descriptions(f"{s['_path']}::{s['run_id']}")
    groups = defaultdict(list)
    for c in rec.get("contracts") or []:
        sc = ev_scores.get(c["ticker"])
        y = sc.get("y") if sc else None
        d = desc.get(c["ticker"]) or {}
        settlement = ({"state": sc["settlement"]["state"], "detail": sc["settlement"].get("detail"), "outcome": y,
                       "settled_at": (sc["settlement"].get("market") or {}).get("settlement_ts"),
                       "score_checksum": "ok" if sc["checksum_ok"] else "MISMATCH"} if sc else
                      {"state": (pending.get(c["ticker"]) or {}).get("state", "NOT_YET_CHECKED"),
                       "detail": (pending.get(c["ticker"]) or {}).get("detail"), "outcome": None})
        groups[c.get("family") or "other"].append({
            "ticker": c["ticker"], "series": c.get("series"), "kind": c.get("kind"), "segment": c.get("segment"),
            "strike": c.get("strike"), "side": c.get("side"), "cohort": c.get("cohort"), "status": c.get("status"),
            "excluded_reason": c.get("reason"), "market_pick": c.get("market_pick"),
            "description": d.get("yes_sub_title") or d.get("title"), "market_title": d.get("title"),
            "rules": d.get("rules_primary"),
            "candidates": {k: _candidate_view((c.get("candidates") or {}).get(k) or {}, k, y) for k in CANDIDATES}
            if c.get("status") == "ok" else None,
            "listing_diagnostics": {"current_yes_bid": (c.get("diagnostics") or {}).get("current_yes_bid"),
                                    "current_yes_ask": (c.get("diagnostics") or {}).get("current_yes_ask"),
                                    "note": "listing snapshot at capture; never a candidate or blend input"}
            if c.get("diagnostics") else None,
            "settlement": settlement,
        })
    order = {f: i for i, f in enumerate(FAMILY_ORDER)}
    families = [{"family": f, "contracts": sorted(v, key=lambda x: (x["status"] != "ok", x["series"] or "",
                                                                    str(x["strike"]), x["ticker"]))}
                for f, v in sorted(groups.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))]
    return {"generated_at": _iso(now), "label": "Saved capture", "source": SOURCE_SAVED, "event": item,
            "families": families, "description_source": {"source": "Kalshi market listing (saved evidence)",
                                                          "files": desc_states},
            "notes": {"binary": BINARY_NOTE, "market_reference": MARKET_NOTE,
                      "market_rule": {k: LC.QUOTE.get(k) for k in ("lookback_hours", "max_age_hours",
                                                                   "period_interval_min", "rule", "reference_time")},
                      "candidates": _candidate_definitions()},
            "missed": ({"reason": rec.get("reason"), "written_at": rec.get("written_at"),
                        "prior_failed_attempts": len(rec.get("prior_failed_attempts") or [])}
                       if s["status"] == "MISSED" else None),
            "provenance": _provenance(rec, s, ev_scores)}


def _candidate_definitions() -> dict:
    from .phase2 import config as P2
    return dict(P2.CANDIDATES)


# ---------------------------------------------------------------- results (descriptive pilot)

def _filter_scores(all_scores: list[dict], f: dict) -> list[dict]:
    return [s for s in all_scores
            if (not f.get("sport") or s.get("sport") == f["sport"])
            and (not f.get("competition") or s.get("competition") == f["competition"])
            and (not f.get("family") or s.get("family") == f["family"])
            and (not f.get("cohort") or s.get("cohort") == f["cohort"])]


def _metric_block(ss: list[dict], weighting: str) -> dict:
    from .scoring import report as RE
    suffix = "_event" if weighting == "event" else ""
    cands = []
    for k in CANDIDATES:
        m = RE.candidate_metrics(ss, k)
        cands.append({"candidate": k, "contracts": m["contracts"], "events": m["events"],
                      "brier": m.get("brier" + suffix), "log_loss": m.get("log_loss" + suffix)})
    pairs = []
    for a, b in SCC.PAIRS:
        m = RE.paired(ss, a, b)
        pairs.append({"candidate": a, "reference": b, "matched_contracts": m["contracts"],
                      "unique_events": m["events"], "d_brier": m.get("brier" + suffix),
                      "d_log_loss": m.get("log_loss" + suffix)})
    return {"candidates": cands, "comparisons": pairs}


def _period(ss: list[dict]) -> dict:
    preds = sorted(s["prediction_as_of"] for s in ss if s.get("prediction_as_of"))
    settled = sorted(t for t in (_settled_at(s) for s in ss) if t)
    return {"first_prediction_as_of": preds[0] if preds else None, "last_prediction_as_of": preds[-1] if preds else None,
            "first_settled_at": settled[0] if settled else None, "last_settled_at": settled[-1] if settled else None}


@_request
def get_results(filters: dict, weighting: str = "event") -> dict:
    weighting = "contract" if weighting == "contract" else "event"
    now = _now()
    loaded = scores()
    all_sc = [s for s in loaded if s["checksum_ok"]]
    sel = _filter_scores(all_sc, filters)
    labelled = [s for s in sel if s.get("y") is not None]
    states = Counter(s["settlement"]["state"] for s in sel)
    breakdown = {}
    for g in SCC.GROUP_KEYS:
        by = defaultdict(list)
        for s in labelled:
            by[str(s.get(g))].append(s)
        breakdown[g] = [{"group": k, **_metric_block(v, weighting)} for k, v in sorted(by.items())]
    return {"generated_at": _iso(now), "label": "Prospective pilot (descriptive, availability-based)",
            "pilot_note": PILOT_LABEL, "sign_note": SIGN_NOTE, "source": SOURCE_SAVED, "weighting": weighting,
            "weighting_note": {"event": "each event's contracts are averaged first, then events count equally",
                               "contract": "every scored contract counts equally"}[weighting],
            "paired_note": "Paired comparisons use only contracts where both the candidate and the reference have "
                           "a probability (common contracts); matched-contract and unique-event counts are shown.",
            "metrics_note": "Brier on the original probability; log loss on clip(p, 1e-4) (frozen Phase 1 formulas).",
            "filters": {k: v for k, v in filters.items() if v},
            "counts": {"final_scores": len(sel), "labelled_contracts": len(labelled),
                       "labelled_events": len({s["event_key"] for s in labelled}),
                       "settlement_states": dict(states),
                       "excluded_score_checksum_problems": sum(1 for s in loaded if not s["checksum_ok"])},
            "period": _period(labelled),
            "facets": {g: sorted({str(s.get(g)) for s in all_sc if s.get(g) is not None}) for g in SCC.GROUP_KEYS},
            "overall": _metric_block(labelled, weighting), "breakdown": breakdown,
            "artifacts": artifacts()}


# ---------------------------------------------------------------- coverage

def _csv_rows(p: Path) -> list[dict] | None:
    def parse(path):
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return _misc.load(p, parse)


@_request
def get_coverage(filters: dict, page: int = 1, page_size: int = 50) -> dict:
    now = _now()
    series = _csv_rows(SERIES_MATRIX_CSV)
    comp_rows = _csv_rows(COMP_MATRIX_CSV)
    inventory = _csv_rows(INVENTORY_CSV)
    titles = {r["series_ticker"]: r.get("series_title") for r in inventory or []}
    out = {"generated_at": _iso(now), "source": {
        "series": "docs/research/sports_capture_v2_series_matrix.csv (tracked; `python -m research.sports.live.run "
                  "matrix`)",
        "competition_family": "docs/research/sports_capture_v2_matrix.csv (tracked)",
        "inventory": "docs/research/sports_inventory_v1.csv (tracked)",
        "observed": SOURCE_SAVED}, "filters": {k: v for k, v in filters.items() if v}}
    if series is None:
        return {**out, "available": False, "missing": "series availability matrix CSV not found", "series": None}
    q = (filters.get("q") or "").strip().lower()

    def keep(r):
        return ((not filters.get("sport") or r["sport"] == filters["sport"])
                and (not filters.get("competition") or filters["competition"] in (r["competition"], r["competition_top"]))
                and (not filters.get("family") or filters["family"] in (r["contract_family"], r["family"]))
                and (not filters.get("status") or r["live_status"] == filters["status"])
                and (not q or q in (r["series_ticker"] + " " + (titles.get(r["series_ticker"]) or "")).lower()))

    sel = [r for r in series if keep(r)]
    page_size = max(1, min(page_size, 500))
    pages = max(1, -(-len(sel) // page_size))
    page = max(1, min(page, pages))
    reasons = defaultdict(Counter)
    for r in sel:
        reasons[r["live_status"]][r["live_reason"] or "(none recorded)"] += 1
    status_counts = Counter(r["live_status"] for r in series)
    comp_sel = [r for r in comp_rows or []
                if (not filters.get("sport") or r["sport"] == filters["sport"])
                and (not filters.get("competition") or r["competition"] == filters["competition"])
                and (not filters.get("family") or r["family"] == filters["family"])]
    observed = {k: {"available": 0, "unavailable": Counter()} for k in CANDIDATES}
    for s in records():
        if s.get("status") != "WINDOW_CAPTURE":
            continue
        if filters.get("sport") and s["sport"] != filters["sport"]:
            continue
        if filters.get("competition") and s["competition"] != filters["competition"]:
            continue
        for c in s["contracts"]:
            if c["status"] != "ok" or (filters.get("family") and c["family"] != filters["family"]):
                continue
            for k in CANDIDATES:
                if c["p"][k]:
                    observed[k]["available"] += 1
                else:
                    observed[k]["unavailable"][c["why"][k] or "(no reason recorded)"] += 1
    return {**out, "available": True,
            "inventory_series_total": len(inventory) if inventory is not None else None,
            "matrix_series_total": len(series),
            "inventory_matches_matrix": (inventory is not None and
                                         {r["series_ticker"] for r in inventory} == {r["series_ticker"] for r in series}),
            "status_counts": dict(status_counts),
            "facets": {"sport": sorted({r["sport"] for r in series if r["sport"]}),
                       "competition": sorted({r["competition"] for r in series if r["competition"]}),
                       "family": sorted({r["contract_family"] for r in series if r["contract_family"]}),
                       "status": sorted(status_counts)},
            "reasons": {k: [{"reason": x, "series": n} for x, n in v.most_common()] for k, v in reasons.items()},
            "series": {"total": len(sel), "page": page, "page_size": page_size, "pages": pages,
                       "items": [{"series_ticker": r["series_ticker"], "series_title": titles.get(r["series_ticker"]),
                                  "sport": r["sport"], "competition": r["competition"] or None,
                                  "competition_top": r["competition_top"], "family": r["contract_family"],
                                  "source": r["source"] or None, "status": r["live_status"],
                                  "reason": r["live_reason"] or None, "comparability": r["comparability"] or None,
                                  "calibration_status": r["calibration_status"] or None,
                                  "blend_status": r["blend_status"] or None,
                                  "market_candidate": r["market_candidate"] or None}
                                 for r in sel[(page - 1) * page_size: page * page_size]]},
            "competition_family": {
                "total": len(comp_rows or []), "shown": len(comp_sel),
                "available_counts": {k: sum(1 for r in comp_rows or [] if r[k] == "available") for k in CANDIDATES},
                "items": [{"competition": r["competition"], "sport": r["sport"], "source": r["source"],
                           "family": r["family"], "live_adapter": r["live_adapter"], "comparability": r["comparability"],
                           "series_supported": int(r["series_supported"] or 0),
                           "series_unsupported": int(r["series_unsupported"] or 0),
                           "candidates": {"frozen": {"status": r["frozen"], "detail": r["frozen_detail"]},
                                          "calibrated": {"status": r["calibrated"], "detail": r["calibration_status"]},
                                          "market": {"status": r["market"], "detail": r["market_rule"]},
                                          "blend": {"status": r["blend"], "detail": r["blend_status"]}}}
                          for r in comp_sel]},
            "observed_in_saved_captures": {k: {"available": v["available"],
                                               "unavailable": [{"reason": x, "contracts": n}
                                                               for x, n in v["unavailable"].most_common()]}
                                           for k, v in observed.items()}}


# ---------------------------------------------------------------- historical research (Phase 1 / Phase 2)

def _historical_source(h: dict) -> dict:
    csv_p = DOCS / h["results_csv"]
    man_p = PROTOCOLS / f"{h['protocol']}.docs_portable_v1.json"
    art_p = PROTOCOLS / f"{h['protocol']}.artifacts.json"
    csv_status = "missing"
    if csv_p.exists():
        man = _read_json(man_p) or {}
        want = (man.get("files") or {}).get(f"docs/research/{h['results_csv']}") or {}
        have = sha256_file(csv_p)
        csv_status = ("verified against tracked docs manifest" if have in (want.get("portable_sha256"),
                                                                           want.get("exact_sha256_in_original_manifest"))
                      else "no manifest entry" if not want else "DIFFERS from tracked docs manifest")
    local = RESULTS_ROOT / h["results_dir"] / h["protocol"] / "metrics.json"
    art = (_read_json(art_p) or {}).get("files") or {}
    want_metrics = art.get(f"data/results/{h['results_dir']}/{h['protocol']}/metrics.json")
    if not local.exists():
        local_status = "absent"
    elif want_metrics is None:
        local_status = "present (no manifest entry)"
    else:
        local_status = "verified" if sha256_file(local) == want_metrics else "DIFFERS from artifact manifest"
    label = ("tracked summary; local raw artifacts verified" if local_status == "verified" else
             "tracked summary only (local raw artifacts absent)" if local_status == "absent" else
             f"tracked summary; local raw artifacts {local_status}")
    return {"label": label, "tracked_csv": csv_status, "local_artifacts": local_status}


def _test_period(proto: dict) -> dict | None:
    """Held-out period the frozen protocol reports results on (Phase 1 test split, Phase 2 EVAL period)."""
    splits = proto.get("splits") or {}
    if splits.get("test_start") and splits.get("test_end"):
        return {"label": "test period", "start": splits["test_start"], "end": splits["test_end"]}
    ev = (proto.get("periods") or {}).get("EVAL")
    if isinstance(ev, list) and len(ev) == 2:
        return {"label": "evaluation period", "start": ev[0], "end": ev[1]}
    return None


@_request
def get_historical(filters: dict, page: int = 1, page_size: int = 100) -> dict:
    now = _now()
    supersedes = {v: k for k, v in EV.PARENT.items()}
    out = []
    for h in HISTORICAL:
        if filters.get("protocol") and filters["protocol"] != h["protocol"]:
            continue
        rows = _csv_rows(DOCS / h["results_csv"])
        proto = _read_json(PROTOCOLS / f"{h['protocol']}.json") or {}
        entry = {"protocol": h["protocol"], "phase": h["phase"], "comparison": h["comparison"],
                 "status": (f"superseded by {supersedes[h['protocol']]}" if h["protocol"] in supersedes else "current"),
                 "frozen_at": proto.get("frozen_at"), "period": _test_period(proto),
                 "report": h["report"], "addenda": h.get("addenda", []),
                 "source": _historical_source(h), "available": rows is not None}
        if rows is None:
            out.append({**entry, "missing": f"{h['results_csv']} not found", "rows": None})
            continue
        sel = [r for r in rows
               if (not filters.get("level") or r.get("level") == filters["level"])
               and (not filters.get("key") or r.get("key") == filters["key"])
               and (not filters.get("family") or r.get("family") == filters["family"])
               and (not filters.get("cohort") or r.get("cohort") == filters["cohort"])
               and (not filters.get("comparison") or r.get("comparison") == filters["comparison"])]
        ps = max(1, min(page_size, 500))
        pages = max(1, -(-len(sel) // ps))
        pg = max(1, min(page, pages))
        entry.update({"columns": list(rows[0].keys()) if rows else [], "total": len(sel), "page": pg,
                      "pages": pages, "page_size": ps, "rows": sel[(pg - 1) * ps: pg * ps],
                      "facets": {k: sorted({r[k] for r in rows if r.get(k)})
                                 for k in ("level", "family", "cohort", "comparison") if rows and k in rows[0]}})
        out.append(entry)
    return {"generated_at": _iso(now), "label": "Historical research (retrospective, frozen protocols)",
            "separation_note": "Historical Phase 1/2 results come from frozen retrospective protocols. They are shown "
                               "separately from the prospective pilot and are never merged with saved captures.",
            "interpretation": EV.INTERPRETATION, "filters": {k: v for k, v in filters.items() if v}, "protocols": out}


# ---------------------------------------------------------------- artifacts, summary, collector

def artifacts() -> dict:
    cap = LC.CAPTURE_DIR / "accepted" / "checkpoint"
    fin = SCC.SCORE_DIR / "finalized"
    return {"capture_records": "present" if cap.exists() and any(cap.glob("*/*.json")) else "missing",
            "settlement_scores": "present" if fin.exists() and any(fin.glob("*/*/*.json")) else "missing",
            "settlement_runs": "present" if latest_settlement_run() else "missing",
            "capture_runs": "present" if latest_capture_run() else "missing",
            "raw_evidence": "present" if LC.EVIDENCE_ROOT.exists() and any(LC.EVIDENCE_ROOT.iterdir()) else "missing"}


def warm() -> None:
    """Parse saved records and scores, then verify the saved title and market evidence of every event
    (default list order first; search needs all of them), ahead of the first requests. Read-only and
    network-free. Evidence parsing holds the interpreter lock, so it pauses while any request runs."""
    items, src = _light_events(_now())
    runs = _title_runs()
    for i in items:
        while _active[0]:
            time.sleep(0.05)
        _enrich(i, src[id(i)], runs)


@_request
def get_summary() -> dict:
    now = _now()
    recs, all_sc = records(), scores()
    items, _ = _light_events(now, recs, all_sc)
    captured = [i for i in items if i["capture_status"] == "WINDOW_CAPTURE"]
    cand = {k: {"available": sum(i["candidate_available"][k] for i in captured),
                "unavailable": sum(i["contracts_ok"] - i["candidate_available"][k] for i in captured)}
            for k in CANDIDATES}
    by_sport = defaultdict(Counter)
    for i in items:
        by_sport[i["sport"] or "unknown"][i["view"]] += 1
    last_cap = max((i["prediction_as_of"] for i in captured if i["prediction_as_of"]), default=None)
    cr = latest_capture_run() or {}
    sr = latest_settlement_run() or {}
    return {
        "generated_at": _iso(now), "label": "Saved captures", "source": SOURCE_SAVED, "mode": "RESEARCH_ONLY",
        "protocol": LC.PROTOCOL, "scoring_protocol": SCC.PROTOCOL, "artifacts": artifacts(),
        "counts": {"captured_events": len(captured),
                   "missed_events": sum(1 for i in items if i["view"] == "missed"),
                   "upcoming_events": sum(1 for i in items if i["view"] == "upcoming"),
                   "scored_events": sum(1 for i in captured if i["settlement"]["labelled"] > 0),
                   "fully_settled_events": sum(1 for i in captured if i["settlement"]["state"] == "settled"),
                   "pending_events": sum(1 for i in captured if i["settlement"]["pending"] > 0),
                   "captured_contracts": sum(i["contracts_ok"] for i in captured),
                   "labelled_contracts": sum(i["settlement"]["labelled"] for i in captured)},
        "candidate_counts": cand,
        "by_sport": {k: dict(v) for k, v in sorted(by_sport.items())},
        "last_capture_at": last_cap,
        "last_capture_run": {"run_id": cr.get("run_id"), "started_at": cr.get("started_at"),
                             "finished_at": cr.get("finished_at"), "counts": cr.get("counts")} if cr else None,
        "last_settlement_run": {"run_id": sr.get("run_id"), "finished_at": sr.get("finished_at"),
                                "counts": sr.get("counts")} if sr else None,
        "integrity": {"record_checksum_mismatches": sum(1 for s in recs if not s.get("checksum_ok", True)),
                      "unreadable_records": sum(1 for s in recs if s.get("unreadable")),
                      "score_checksum_mismatches": sum(1 for s in all_sc if not s["checksum_ok"])},
        "collector": collector_brief(),
        "collection": _collection_summary(items),
    }


def _collection_summary(items: list[dict]) -> dict:
    from .ops import provenance as PV
    by = {m: {"captured": 0, "missed": 0} for m in PV.MODES}
    for i in items:
        by[i["collection_mode"]]["captured" if i["capture_status"] == "WINDOW_CAPTURE" else "missed"] += 1
    return {"by_mode": by, "labels": PV.MODE_LABELS,
            "amendment": {k: PV.AMENDMENT[k] for k in ("id", "date", "doc", "cadence_minutes", "collector_commit",
                                                       "first_scheduled_action_at", "first_trigger_run_at")}}


def collector_brief() -> dict:
    from .ops import collector as CO
    st = _read_json(CO.STATUS_PATH)
    if not st:
        return {"recorded": False, "note": "no automatic collector cycle recorded on this machine"}
    last = st.get("last_cycle") or {}
    return {"recorded": True, "last_cycle_finished_at": last.get("finished_at"), "last_cycle_result": last.get("result"),
            "last_successful_capture_at": st.get("last_successful_capture_at"),
            "last_settlement_check_at": st.get("last_settlement_check_at"),
            "consecutive_failures": st.get("consecutive_failures")}


def _backup_brief() -> dict:
    from .ops import backup as BK
    st = BK.read_status()
    if not st:
        return {"recorded": False, "note": "no sports state backup recorded on this machine"}
    return {"recorded": True, **{k: st.get(k) for k in ("backup", "at", "finished_at", "files", "bytes",
                                                          "missing_count", "incomplete", "local_head", "ref")}}


def _cycle_history(cycles_path: Path) -> dict:
    """Latest cycle and latest successful cycle over the whole cycle log (the status file covers 24 h only)."""
    last = last_ok = None
    try:
        with open(cycles_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("result") == "skipped_lock_busy":
                    continue
                last = r
                if r.get("result") == "ok":
                    last_ok = r
    except OSError:
        return {"recorded": False}
    return {"recorded": last is not None,
            "last_cycle_finished_at": (last or {}).get("finished_at"), "last_cycle_result": (last or {}).get("result"),
            "last_successful_cycle_at": (last_ok or {}).get("finished_at")}


@_request
def get_collector_status(task_query=None) -> dict:
    from .ops import collector as CO
    from .ops import lock as LK
    from .ops import scheduler as SCH
    now = _now()
    query = task_query or SCH.query_task
    st = _read_json(CO.STATUS_PATH)
    recs = records()
    missed = [s for s in recs if s.get("status") == "MISSED"]
    missed_24h = [s for s in missed if (_ts(s.get("written_at")) or 0) >= now - 86400]
    cap_times = [s["finalized_at"] for s in recs if s.get("status") == "WINDOW_CAPTURE" and s.get("finalized_at")]
    sc_times = [s.get("scored_at") for s in scores() if s.get("scored_at")]
    last_rec = max(cap_times, default=None)
    last_score = max(sc_times, default=None)

    def age(t):
        return None if not t else round((now - _ts(t)) / 60, 1)

    return {
        "generated_at": _iso(now), "task_name": CO.TASK_NAME,
        "scheduler": query(CO.TASK_NAME),
        "weather_task": query("KalshiEdgeLab-WeatherProspective"),
        "lock": LK.lock_state(LK.LOCK_PATH),
        "cadence_minutes": CO.CADENCE_MIN, "settlement_interval_minutes": CO.SETTLE_INTERVAL_S // 60,
        "status_recorded": st is not None,
        "status": st or {"note": "no automatic collector cycle recorded on this machine"},
        "missed_windows": {"total": len(missed), "last_24h": len(missed_24h),
                           "recent": [{"event_id": s["event_id"], "scheduled_cutoff": s.get("scheduled_cutoff"),
                                       "written_at": s.get("written_at"), "reason": s.get("reason")}
                                      for s in sorted(missed, key=lambda x: x.get("written_at") or "")[-10:]]},
        "freshness": {"last_accepted_capture_at": last_rec, "minutes_since_last_capture": age(last_rec),
                      "last_score_written_at": last_score, "minutes_since_last_score": age(last_score),
                      "last_cycle_finished_at": ((st or {}).get("last_cycle") or {}).get("finished_at"),
                      "minutes_since_last_cycle": age(((st or {}).get("last_cycle") or {}).get("finished_at"))},
        "history": _cycle_history(CO.CYCLES_PATH),
        "backup": _backup_brief(),
        "limits": "Captures need the machine awake, logged in and online during each event's 20-minute window. "
                  "Sleep, shutdown, logoff or an internet failure produce MISSED records; missed forecasts are "
                  "never reconstructed from later downloads.",
    }
