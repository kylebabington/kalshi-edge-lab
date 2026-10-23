"""ITF Elo diagnosis on the frozen October 21 snapshot.

Capture-time history is used only when the capture record, pins, and evidence
bodies verify. Frozen-normalized replay is a lower bound and is labeled reconstructed.
A reconstructed n=0 does not establish that the live prediction was a cold start.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from .. import adapters as ad
from .. import evaluate as EV
from .. import models as M
from ..build import MilestoneIndex
from ..core import NORMALIZED, read_jsonl
from ..engines import Elo, replay
from ..live import config as LC
from ..live import discover as D
from ..live import sources as S
from ..phase2 import folds as F
from ..scoring import score as SC
from .constants import (
    COMPETITIONS, DIAGNOSIS_ITF_CONTRIB, DIAGNOSIS_ITF_EVENTS, DIAGNOSIS_N_EVENTS,
    P_AGREE_TOL, REVIEW_CUTOFF, SNAP,
)
from .inputs import (
    InputError, evidence_bodies_ok, read_verified_snapshot, verify_capture_record,
)


SURFACE_KEYS = ("surface", "court", "court_type", "courtType", "ground", "indoor")


class InspectElo(Elo):
    """Same Elo updates as the frozen model, with inspectable counts and ratings."""

    def inspect(self, a: str, b: str, t: float) -> dict:
        seen_a, seen_b = a in self.r, b in self.r
        ra, rb = self.rating(a, t), self.rating(b, t)
        last_a, last_b = self.last.get(a), self.last.get(b)
        return {
            "n_a": int(self.n[a]),
            "n_b": int(self.n[b]),
            "seen_a": seen_a,
            "seen_b": seen_b,
            "r_a": ra,
            "r_b": rb,
            "d": ra - rb,
            "p": self.prob(a, b, t, True),
            "last_a": last_a,
            "last_b": last_b,
            "days_since_a": None if last_a is None else (t - last_a) / 86400.0,
            "days_since_b": None if last_b is None else (t - last_b) / 86400.0,
        }


def parse_ts(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def load_frozen_events(key: str) -> list[dict]:
    return list(read_jsonl(NORMALIZED / key / "events.jsonl"))


def event_keys_union(events: list[dict]) -> list[str]:
    keys = set()
    for e in events[:5000]:
        keys.update(e.keys())
    return sorted(keys)


def surface_fields_present(keys: list[str]) -> list[str]:
    found = []
    lower = {k.lower(): k for k in keys}
    for name in SURFACE_KEYS:
        if name.lower() in lower:
            found.append(lower[name.lower()])
    return found


def load_itf_matched_scores(listing: dict) -> list[dict]:
    rows = []
    for comp in COMPETITIONS:
        root = SNAP / "sports_scores" / "finalized" / comp
        if not root.is_dir():
            raise InputError(f"snapshot scores missing: {root}")
        for p in sorted(root.glob("*/*.json")):
            live = "data/sports/phase2/capture_v2_scores/" + p.relative_to(SNAP / "sports_scores").as_posix()
            _, s = read_verified_snapshot(live, listing)
            if s.get("y") not in (0, 1):
                continue
            fz = (s.get("candidates") or {}).get("frozen") or {}
            mk = (s.get("candidates") or {}).get("market") or {}
            if "p" not in fz or "p" not in mk:
                continue
            if not SC.valid_p(fz["p"]) or not SC.valid_p(mk["p"]):
                raise InputError(f"invalid probability on {s.get('ticker')}")
            lf = EV._losses({"p": float(fz["p"]), "y": int(s["y"])})
            lm = EV._losses({"p": float(mk["p"]), "y": int(s["y"])})
            rows.append({
                "ticker": s["ticker"], "event_key": s["event_key"], "competition": s["competition"],
                "family": s.get("family"), "y": int(s["y"]), "frozen_p": float(fz["p"]),
                "market_p": float(mk["p"]), "d_brier": lf[0] - lm[0], "d_log": lf[1] - lm[1],
                "prediction_as_of": s.get("prediction_as_of"),
                "source_record": s.get("source_record"),
                "source_record_sha256": s.get("source_record_sha256"),
                "cluster": s.get("cluster"),
            })
    n_e = defaultdict(int)
    for r in rows:
        n_e[r["event_key"]] += 1
    for r in rows:
        r["n_e"] = n_e[r["event_key"]]
        r["w_event"] = 1.0 / (DIAGNOSIS_N_EVENTS * r["n_e"])
    rows.sort(key=lambda r: (r["competition"], r["event_key"], r["ticker"]))
    return rows


def load_capture(live_rel: str, listing: dict) -> dict:
    _, rec = read_verified_snapshot(live_rel, listing)
    verify_capture_record(rec)
    return rec


def recover_capture_history(comp, rec: dict, frozen: list[dict]) -> tuple[list[dict] | None, str, dict]:
    """Return (events, status, stats). status is capture_time or a failure reason."""
    ok, why, present, missing = evidence_bodies_ok(rec)
    stats = {"evidence_present": present, "evidence_missing": missing}
    if not ok:
        return None, f"evidence_unusable: {why}", stats
    saved = D.SavedEvidence(LC.EVIDENCE_ROOT / rec["run_id"], rec["evidence"])
    try:
        ms_rows, st = D.milestones(saved.get)
    except D.EvidenceError as exc:
        return None, f"milestones_not_in_evidence: {exc}", stats
    if not st["complete"]:
        return None, f"milestone_pagination_incomplete: {st.get('error')}", stats
    mindex = MilestoneIndex(ms_rows)
    min_close = int(S.history_start(comp, frozen).timestamp())
    settled = []
    for series, _, _ in comp.explicit_series:
        try:
            items, st = D.settled_markets(saved.get, series, min_close)
        except D.EvidenceError as exc:
            return None, f"settled_markets_not_in_evidence: {exc}", stats
        if not st["complete"]:
            return None, f"settled_pagination_incomplete: {st.get('error')}", stats
        settled += items
    live, hist_stats = S.kalshi_only_history(comp, settled, mindex)
    by_id = {}
    for e in live:
        gid = e["source_game_id"]
        if gid in by_id:
            continue
        by_id[gid] = e
    live = list(by_id.values())
    events = S.merge(frozen, live)
    stats.update({"live_events": len(live), "merged_events": len(events), **hist_stats})
    return events, "capture_time", stats


def inspect_at_queries(events: list[dict], queries: list[tuple], params: dict) -> dict:
    """Same interleaving as models._konly_run: releases by available_at, query at start-60min, inspect at start."""
    elo = InspectElo(params["k"], 0, params.get("carry") or 0.0, False, gap_days=120)
    out = {}

    def upd(e):
        elo.update(e["home"], e["away"], M.ts(e["start"]),
                   1.0 if e["winner_id"] == e["home"] else 0.0, True)

    def pred(q):
        key, a, b, t = q
        out[key] = elo.inspect(a, b, t)

    replay(M.konly_releases(events), [(q[3] - M.HORIZON_MIN * 60, q) for q in queries], upd, pred)
    return out


def frozen_p_from_predict_konly(comp, events: list[dict], ticker: str, start: str,
                                participant: str, opponent: str, params: dict) -> float | None:
    """Unmodified Phase 1 kalshi-only predictor (contract-side = participant vs opponent)."""
    home, away = sorted([participant, opponent])
    contracts = [{"ticker": ticker, "status": "ok", "start": start, "home": home, "away": away,
                  "participant": participant, "family": "game_winner"}]
    with F.fold_window(M.T_TEST, M.ts(LC.LIVE_T_END)):
        preds = M.predict_konly(comp, events, contracts, {"params": params})
    row = F.primary_row(preds, "game_winner")
    return None if row is None else float(row["p"])


def contract_side(rec: dict, ticker: str) -> tuple[str | None, str | None, str | None]:
    """Map a ticker to (participant, opponent, reason). Uses capture-record participants."""
    cs = [c for c in rec.get("contracts") or [] if c.get("status") == "ok"]
    by_t = {c["ticker"]: c for c in cs}
    row = by_t.get(ticker)
    if row is None:
        return None, None, "ticker_not_on_capture_record"
    a = row.get("participant")
    others = sorted({c.get("participant") for c in cs if c.get("participant") and c["ticker"] != ticker})
    if not a:
        return None, None, "capture_contract_missing_participant"
    if len(others) != 1:
        return a, None, f"expected_one_opponent_got_{len(others)}"
    return a, others[0], None


def as_of_view_match(comp, events, rec) -> dict:
    as_of = rec["prediction_as_of_ts"]
    view, vstats = S.as_of_view(comp, events, as_of)
    saved = rec.get("as_of_view") or {}
    return {
        "results_used": vstats.get("results_used"),
        "events_hidden": vstats.get("events_hidden"),
        "saved_results_used": saved.get("results_used"),
        "saved_events_hidden": saved.get("events_hidden"),
        "view_events": len(view),
        "results_used_match": vstats.get("results_used") == saved.get("results_used"),
    }


def identity_table(uuids: set[str], targets: dict | None) -> list[dict]:
    rows = []
    if targets is None:
        return [{"participant": u, "name": None, "identity_status": "structured_targets_unavailable",
                 "same_name_uuid_count": None} for u in sorted(uuids)]
    name_to = defaultdict(list)
    for u in uuids:
        t = targets.get(u) or {}
        name = t.get("name") or None
        if name:
            name_to[name].append(u)
    for u in sorted(uuids):
        t = targets.get(u) or {}
        name = t.get("name") or None
        status = "named" if name else ("unresolved_missing_from_structured_targets" if u not in targets
                                       else "unresolved_empty_name")
        n_same = len(name_to[name]) if name else 0
        if n_same > 1:
            status = "name_collision_review_candidate"
        rows.append({
            "participant": u, "name": name, "short_name": t.get("short_name"),
            "first_name": t.get("first_name"), "last_name": t.get("last_name"),
            "identity_status": status, "same_name_uuid_count": n_same or None,
            "merged": False,
        })
    return rows


def n_bin(n) -> str:
    if n is None:
        return "unknown"
    if n == 0:
        return "n=0"
    if n <= 2:
        return "n=1-2"
    if n <= 9:
        return "n=3-9"
    if n <= 29:
        return "n=10-29"
    return "n>=30"


def abs_d_bin(d) -> str:
    if d is None:
        return "unknown"
    x = abs(d)
    if x < 20:
        return "|d|<20"
    if x < 50:
        return "|d|=20-50"
    if x < 100:
        return "|d|=50-100"
    return "|d|>=100"


def partition(rows: list[dict], key: str) -> list[dict]:
    by = defaultdict(list)
    for r in rows:
        by[r.get(key) or "unknown"].append(r)
    out = []
    for g in sorted(by):
        rs = by[g]
        events = sorted({r["event_key"] for r in rs})
        out.append({
            "group_by": key, "group": g, "contracts": len(rs), "events": len(events),
            "contrib_event_equal": math.fsum(r["d_brier"] * r["w_event"] for r in rs),
            "mean_abs_d": (math.fsum(abs(r["rating_diff"]) for r in rs if r.get("rating_diff") is not None)
                           / max(1, sum(1 for r in rs if r.get("rating_diff") is not None))),
            "mean_abs_p_minus_half": (math.fsum(abs(r["frozen_p"] - 0.5) for r in rs) / len(rs)),
            "p_agree_count": sum(1 for r in rs if r.get("p_agree") is True),
        })
    return out


def diagnose(static: dict) -> dict:
    listing = static["listing"]
    comps = static["competitions"]
    frozen = {k: load_frozen_events(k) for k in COMPETITIONS}
    event_fields = {k: event_keys_union(frozen[k]) for k in COMPETITIONS}
    surface = {k: surface_fields_present(event_fields[k]) for k in COMPETITIONS}

    scores = load_itf_matched_scores(listing)
    contrib = {}
    for comp in COMPETITIONS:
        rs = [r for r in scores if r["competition"] == comp]
        contrib[comp] = math.fsum(r["d_brier"] * r["w_event"] for r in rs)

    try:
        targets = ad.load_structured_targets()
    except (OSError, ValueError, KeyError):
        targets = None

    history_cache: dict[tuple, tuple] = {}
    capture_cache: dict[str, dict | Exception] = {}
    replay_rows = []
    uuids: set[str] = set()
    capture_surface_keys = set()
    by_record = defaultdict(list)
    for r in scores:
        by_record[r["source_record"]].append(r)

    n_records = len(by_record)
    for i, (rec_rel, group) in enumerate(by_record.items(), 1):
        if i == 1 or i % 50 == 0 or i == n_records:
            print(f"itf replay {i}/{n_records}", flush=True)
        blank = {
            "history_kind": "unavailable", "replay_status": "stopped_missing_input", "replay_reason": None,
            "p_replay": None, "p_agree": None, "p_abs_diff": None,
            "n_participant": None, "n_opponent": None, "min_n": None,
            "seen_participant": None, "seen_opponent": None,
            "rating_participant": None, "rating_opponent": None, "rating_diff": None,
            "days_since_participant": None, "days_since_opponent": None,
            "carry_enabled": False, "results_used_match": None,
            "participant": None, "opponent": None,
        }
        if rec_rel not in capture_cache:
            try:
                capture_cache[rec_rel] = load_capture(rec_rel, listing)
            except InputError as exc:
                capture_cache[rec_rel] = exc
        rec = capture_cache[rec_rel]
        if isinstance(rec, Exception):
            for r in group:
                replay_rows.append({**blank, **r, "replay_reason": f"capture_input: {rec}",
                                    "surface_fields": ",".join(surface[r["competition"]]) or ""})
            continue
        want_sha = group[0].get("source_record_sha256")
        if want_sha and rec.get("record_sha256") != want_sha:
            for r in group:
                replay_rows.append({**blank, **r, "replay_reason": "source_record_sha256 mismatch",
                                    "surface_fields": ",".join(surface[r["competition"]]) or ""})
            continue
        for c in rec.get("contracts") or []:
            capture_surface_keys.update(k for k in c.keys() if k.lower() in SURFACE_KEYS)
            if c.get("participant"):
                uuids.add(c["participant"])
        comp = comps[group[0]["competition"]]
        hkey = (comp.key, rec["run_id"])
        if hkey not in history_cache:
            history_cache[hkey] = recover_capture_history(comp, rec, frozen[comp.key])
        events, status, stats = history_cache[hkey]
        params = ((rec.get("model") or {}).get("params") or {}).get("params") or {"k": 24, "carry": 0.0}
        as_of = rec["prediction_as_of_ts"]
        if events is not None and status == "capture_time":
            history_kind = "capture_time"
            used_events = events
            view_info = as_of_view_match(comp, events, rec)
            recon_reason = None
        else:
            history_kind = "reconstructed_frozen_only"
            used_events = frozen[comp.key]
            view_info = as_of_view_match(comp, used_events, rec)
            recon_reason = status
        view, _ = S.as_of_view(comp, used_events, as_of)
        queries, sides = [], {}
        stopped = []
        for r in group:
            a, b, why = contract_side(rec, r["ticker"])
            if why or parse_ts(rec.get("start")) is None:
                stopped.append((r, a, b, why or "capture_missing_start"))
                continue
            sides[r["ticker"]] = (a, b)
            queries.append((r["ticker"], a, b, parse_ts(rec["start"])))
        inspected = inspect_at_queries(view, queries, params) if queries else {}
        for r, a, b, why in stopped:
            replay_rows.append({**blank, **r, "participant": a, "opponent": b, "replay_reason": why,
                                "surface_fields": ",".join(surface[r["competition"]]) or ""})
        for r in group:
            if r["ticker"] not in inspected:
                continue
            info = inspected[r["ticker"]]
            a, b = sides[r["ticker"]]
            p_replay = info["p"]
            replay_rows.append({
                **r, "history_kind": history_kind,
                "replay_status": "ok" if history_kind == "capture_time" else "reconstructed",
                "replay_reason": recon_reason, "participant": a, "opponent": b,
                "p_replay": p_replay, "p_abs_diff": abs(p_replay - r["frozen_p"]),
                "p_agree": abs(p_replay - r["frozen_p"]) <= P_AGREE_TOL,
                "n_participant": info["n_a"], "n_opponent": info["n_b"],
                "min_n": min(info["n_a"], info["n_b"]),
                "seen_participant": info["seen_a"], "seen_opponent": info["seen_b"],
                "rating_participant": info["r_a"], "rating_opponent": info["r_b"],
                "rating_diff": info["d"],
                "days_since_participant": info["days_since_a"],
                "days_since_opponent": info["days_since_b"],
                "carry_enabled": bool(params.get("carry")),
                "results_used_match": view_info["results_used_match"] if history_kind == "capture_time" else False,
                "as_of_results_used": view_info.get("results_used"),
                "saved_results_used": view_info.get("saved_results_used"),
                "history_stats_live_events": (stats or {}).get("live_events"),
                "surface_fields": ",".join(surface[r["competition"]]) or "",
            })

    for r in replay_rows:
        r["agreement_slice"] = (
            "capture_time_agree" if r["history_kind"] == "capture_time" and r.get("p_agree") else
            "capture_time_disagree" if r["history_kind"] == "capture_time" else
            "reconstructed_agree" if r["history_kind"] == "reconstructed_frozen_only" and r.get("p_agree") else
            "reconstructed_disagree" if r["history_kind"] == "reconstructed_frozen_only" else
            "replay_stopped"
        )
        r["n_bin"] = n_bin(r.get("min_n"))
        r["abs_d_bin"] = abs_d_bin(r.get("rating_diff"))
        r["history_complete"] = r["history_kind"] == "capture_time" and r.get("results_used_match") is True

    ident = identity_table(uuids, targets)
    parts = []
    parts += partition(replay_rows, "competition")
    parts += partition(replay_rows, "agreement_slice")
    parts += partition(replay_rows, "history_kind")
    agreed = [r for r in replay_rows if r["agreement_slice"] == "capture_time_agree"]
    parts += partition(agreed, "n_bin")
    parts += partition(agreed, "abs_d_bin")
    parts += partition(agreed, "history_complete")

    return {
        "scores": scores,
        "replay_rows": replay_rows,
        "partitions": parts,
        "identity": ident,
        "contrib": contrib,
        "event_fields": event_fields,
        "surface_on_events": surface,
        "surface_on_capture_contracts": sorted(capture_surface_keys),
        "structured_targets_loaded": targets is not None,
        "n_replay": len(replay_rows),
        "n_capture_time": sum(1 for r in replay_rows if r["history_kind"] == "capture_time"),
        "n_agree_capture": sum(1 for r in replay_rows if r["agreement_slice"] == "capture_time_agree"),
        "expected_events": DIAGNOSIS_ITF_EVENTS,
        "expected_contrib": DIAGNOSIS_ITF_CONTRIB,
        "p_agree_tol": P_AGREE_TOL,
        "review_cutoff": REVIEW_CUTOFF,
    }
