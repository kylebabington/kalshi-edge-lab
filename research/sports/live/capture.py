"""Manual prospective capture (``sports_phase2_capture_v2``). Never scheduled; no backfill.

Per run: pins -> milestone discovery (complete pagination) -> per competition: live markets, history
since the freeze, linking -> per event in scope of the requested mode: provisional market picks,
candle requests, prediction_as_of, the four frozen candidates, timing checks, then storage:

* ``accepted/<checkpoint|early>/<competition>/<event>.json`` - created exclusively, never rewritten.
  The first capture satisfying every recording rule is accepted whatever its probabilities.
* ``attempts/<run_id>/...`` - FAILED_ATTEMPT (rule broken: network, pagination, pins, timing) and
  DUPLICATE_ATTEMPT (accepted record already exists). Failed attempts never occupy the slot, so a
  later attempt inside the same window may still be accepted.
* MISSED is written (window mode only) after the window has expired without an accepted capture.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import evaluate as EV
from ..competitions import all_competitions
from ..core import PROTOCOLS, REPO_ROOT, sha256_file, sha256_json
from . import config as C
from . import discover as D
from . import pins as PN
from . import predict as P

IDEMPOTENCY = {
    "key": "(mode slot, competition, event cluster); slot = checkpoint (window mode) or early",
    "accepted": "first record meeting every recording rule; exclusive create; never replaced",
    "failed": "FAILED_ATTEMPT records under attempts/<run_id>/; they never occupy the accepted slot",
    "duplicate": "DUPLICATE_ATTEMPT under attempts/<run_id>/ referencing the accepted record's sha256",
    "missed": "MISSED accepted only in window mode after now > cutoff without an accepted capture, for events "
              "discovered before their start or with earlier failed attempts",
}


def _iso_us(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _relpath(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def safe(cluster: str) -> str:
    return cluster.replace(":", "_").replace("/", "_")


def slot(mode: str) -> str:
    return "checkpoint" if mode == "window" else "early"


def accepted_path(mode: str, comp_key: str, cluster: str) -> Path:
    return C.CAPTURE_DIR / "accepted" / slot(mode) / comp_key / f"{safe(cluster)}.json"


def attempts_dir(run_id: str) -> Path:
    return C.CAPTURE_DIR / "attempts" / run_id


def seal(rec: dict) -> dict:
    rec = json.loads(json.dumps(rec))
    rec["record_sha256"] = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    return rec


def _bytes(rec: dict) -> bytes:
    return (json.dumps(rec, sort_keys=True, indent=1) + "\n").encode("utf-8")


def write_accepted(path: Path, rec: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as f:
            f.write(_bytes(rec))
        return True
    except FileExistsError:
        return False


def write_attempt(run_id: str, name: str, rec: dict) -> Path:
    d = attempts_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    n = 1
    while p.exists():
        n += 1
        p = d / f"{name}__{n}.json"
    with open(p, "xb") as f:
        f.write(_bytes(rec))
    return p


def prior_attempts(comp_key: str, cluster: str) -> list[str]:
    root = C.CAPTURE_DIR / "attempts"
    if not root.exists():
        return []
    return sorted(p.relative_to(C.CAPTURE_DIR).as_posix()
                  for p in root.glob(f"*/{comp_key}__{safe(cluster)}__FAILED_ATTEMPT*.json"))


# ---------------------------------------------------------------- deterministic computation (capture == replay)

def compute_event(comp, entry, groups, ctx, cluster: str, t_pick: float, as_of: float, candles: dict) -> dict:
    """``candles``: ticker -> (raw, request_ts). Returns contracts with candidates, picks and view stats."""
    contracts = P.clusters(ctx)[cluster]
    prov, _ = P.predict_frozen(comp, entry, ctx["events"], contracts, t_pick)
    picks = P.market_picks(comp, entry, contracts, prov, ctx["raw_by_ticker"], t_pick)
    frozen, vstats = P.predict_frozen(comp, entry, ctx["events"], contracts, as_of)
    quotes = {t: P.quote(candles[t][0], as_of, candles[t][1]) if t in candles else
              {"unavailable": "candle request missing"} for t in picks}
    out = P.assemble(comp, entry, groups, contracts, frozen, picks, quotes, ctx["raw_by_ticker"])
    return {"contracts": out, "market_picks": picks, "as_of_view": vstats}


# ---------------------------------------------------------------- run

class Clock:
    def __init__(self, now_fn):
        self.now_fn = now_fn

    def now(self) -> datetime:
        return self.now_fn()

    def t(self) -> float:
        return self.now_fn().timestamp()


def _comp_maps():
    comps, specs, _ = all_competitions()
    by_key = {c.key: c for c in comps}
    series_spec = {k: {s: (kind, seg) for s, kind, seg in v} for k, v in specs.items()}
    series_comp = {s: k for k, v in series_spec.items() for s in v}
    return by_key, series_spec, series_comp


def run(mode: str, competitions: str | None, log, now_fn=None, session=None, lookahead_h: float | None = None) -> int:
    if mode not in C.MODES:
        raise SystemExit(f"--mode must be one of {sorted(C.MODES)}")
    clock = Clock(now_fn or (lambda: datetime.now(timezone.utc)))
    started = clock.now()
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
    by_key, series_spec, series_comp = _comp_maps()
    wanted = None if not competitions else {k.strip() for k in competitions.split(",") if k.strip()}
    summary = {"schema": "sports_phase2_capture_v2_run", "protocol": C.PROTOCOL, "run_id": run_id, "mode": mode,
               "started_at": _iso_us(started.timestamp()), "competitions_requested": sorted(wanted) if wanted else "all",
               "events": [], "problems": [], "research_only": True, "no_bet": True}

    def finish(code: int) -> int:
        summary["finished_at"] = _iso_us(clock.t())
        counts = {}
        for e in summary["events"]:
            counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
        summary["counts"] = counts
        p = C.CAPTURE_DIR / "runs" / f"{run_id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_bytes(summary))
        log(f"capture {mode} run {run_id}: {counts or 'no events'}; problems={len(summary['problems'])}; summary {p}")
        return code

    def diag(name: str, reasons: list[str], extra: dict | None = None):
        rec = seal({"schema": C.SCHEMA, "protocol": C.PROTOCOL, "status": "FAILED_ATTEMPT", "mode": mode,
                    "run_id": run_id, "written_at": _iso_us(clock.t()), "failure_reasons": reasons, **(extra or {})})
        return write_attempt(run_id, name, rec)

    try:
        proto = PN.load_protocol()
    except SystemExit as exc:
        summary["problems"].append(str(exc))
        diag("_run__FAILED_ATTEMPT", [str(exc)])
        return finish(2)
    pin_problems = PN.check(proto, [])
    if pin_problems:
        summary["problems"] += pin_problems
        diag("_run__FAILED_ATTEMPT", ["pin failure"] + pin_problems)
        return finish(2)
    proto_sha = sha256_file(PN.protocol_path())
    proto1 = EV.load_protocol(C.PHASE1_PROTOCOL)
    groups = json.loads((PROTOCOLS / f"{C.PARENT_PROTOCOL}.json").read_text(encoding="utf-8"))["groups"]

    ev = D.Evidence(C.EVIDENCE_ROOT / run_id, clock.now, session)
    tr = D.Tracker(ev.get)
    ms_rows, st = D.milestones(tr.get)
    summary["discovery"] = {"milestones": len(ms_rows), **st}
    if not st["complete"]:
        summary["problems"].append(f"milestone discovery incomplete: {st['error']}")
        diag("_run__FAILED_ATTEMPT", [f"milestone discovery incomplete: {st['error']}"], {"evidence": ev.refs(tr.keys)})
        return finish(2)
    now = clock.t()
    if mode == "window":
        lo, hi = now + C.WINDOW_DISCOVERY_H[0] * 3600, now + C.WINDOW_DISCOVERY_H[1] * 3600
    else:
        la = min(float(lookahead_h or C.EARLY_LOOKAHEAD_H["default"]), C.EARLY_LOOKAHEAD_H["max"])
        lo, hi = now + (C.HORIZON_MIN + C.WINDOW_MIN) * 60, now + la * 3600
    summary["discovery"]["milestone_start_range"] = [_iso(lo), _iso(hi)]
    per_comp: dict[str, set] = {}
    latest: dict[str, float] = {}
    for m in ms_rows:
        if not m.get("start") or not (lo <= _ts(m["start"]) <= hi):
            continue
        for et in m["event_tickers"]:
            k = series_comp.get(et.split("-")[0])
            if k and (wanted is None or k in wanted):
                per_comp.setdefault(k, set()).add(et)
                latest[k] = max(latest.get(k, 0.0), _ts(m["start"]))
    unknown = sorted(wanted - set(by_key)) if wanted else []
    if unknown:
        summary["problems"].append(f"unknown competitions {unknown}")
    log(f"discovery: {len(ms_rows)} milestones; {sum(len(v) for v in per_comp.values())} Kalshi events in "
        f"{len(per_comp)} competitions within [{_iso(lo)}, {_iso(hi)}]")

    for key in sorted(per_comp):
        comp = by_key[key]
        ad_rule = C.ADAPTERS[comp.source]
        entry = proto1["competitions"].get(key)
        base = {"competition": key, "source": comp.source}
        if ad_rule["status"] != "supported":
            summary["events"].append({**base, "event_tickers": sorted(per_comp[key]), "outcome": "unsupported_adapter",
                                      "reason": ad_rule["reason"]})
            continue
        if entry is None or entry.get("blocked") or (entry.get("params") or {}).get("blocked"):
            summary["events"].append({**base, "event_tickers": sorted(per_comp[key]), "outcome": "no_frozen_model",
                                      "reason": "no unblocked sports_phase1_v2 model"})
            continue
        probs = PN.check(proto, [comp])
        if probs:
            summary["problems"] += probs
            diag(f"{key}___competition__FAILED_ATTEMPT", ["pin failure"] + probs)
            summary["events"].append({**base, "outcome": "failed_attempt", "reason": "pin failure"})
            continue
        history_end = _iso(max(clock.t(), latest[key]) + 86400 + (3 * 86400 if comp.source == "espn_golf" else 0))
        tc = D.Tracker(ev.get)
        try:
            ctx = P.build_context(comp, tc.get, sorted(per_comp[key]), history_end, series_spec[key])
        except Exception as exc:  # malformed source data must not stop other competitions
            why = f"context build error: {type(exc).__name__}: {exc}"
            summary["problems"].append(f"{key}: {why}")
            diag(f"{key}___competition__FAILED_ATTEMPT", [why], {"competition": key, "evidence": ev.refs(tc.keys)})
            summary["events"].append({**base, "outcome": "failed_attempt", "reason": why})
            continue
        comp_keys = list(tc.keys)
        ctx_meta = {"event_tickers": sorted(per_comp[key]), "history_end": history_end}
        if ctx["problems"]:
            summary["problems"] += [f"{key}: {x}" for x in ctx["problems"]]
            diag(f"{key}___competition__FAILED_ATTEMPT", ctx["problems"],
                 {"competition": key, "evidence": ev.refs(comp_keys)})
        by_cluster = P.clusters(ctx)
        unlinked = [c for c in ctx["contracts"] if not c.get("cluster") or not c.get("start")]
        if unlinked:
            summary["events"].append({**base, "outcome": "unlinked_contracts", "contracts": len(unlinked),
                                      "reasons": sorted({c.get("reason") or "" for c in unlinked})})
        for cl in sorted(by_cluster, key=lambda c: (min(_ts(x["start"]) for x in by_cluster[c]), c)):
            cs = by_cluster[cl]
            start = min(_ts(x["start"]) for x in cs)
            cutoff = start - C.HORIZON_MIN * 60
            w_open = cutoff - C.WINDOW_MIN * 60
            ets = sorted({x["event_ticker"] for x in cs})
            ev_base = {**base, "cluster": cl, "start": _iso(start), "scheduled_cutoff": _iso(cutoff),
                       "window_open": _iso(w_open), "kalshi_event_tickers": ets}
            if all(x["status"] != "ok" for x in cs):
                summary["events"].append({**ev_base, "outcome": "no_linked_contracts",
                                          "reasons": sorted({x.get("reason") or "" for x in cs})})
                continue
            t_now = clock.t()
            acc = accepted_path(mode, key, cl)
            if mode == "window":
                if t_now < w_open:
                    summary["events"].append({**ev_base, "outcome": "window_not_open"})
                    continue
                if t_now > cutoff:
                    prior = prior_attempts(key, cl)
                    if acc.exists():
                        summary["events"].append({**ev_base, "outcome": "accepted_exists"})
                    elif start > t_now or prior:
                        rec = seal({"schema": C.SCHEMA, "protocol": C.PROTOCOL, "status": "MISSED", "mode": mode,
                                    "run_id": run_id, **ev_base, "written_at": _iso_us(t_now),
                                    "reason": "checkpoint window expired without an accepted capture",
                                    "prior_failed_attempts": prior, "capture_protocol_sha256": proto_sha})
                        ok = write_accepted(acc, rec)
                        summary["events"].append({**ev_base, "outcome": "MISSED" if ok else "accepted_exists"})
                    else:
                        summary["events"].append({**ev_base, "outcome": "started_not_recorded"})
                    continue
            else:
                if t_now >= w_open:
                    summary["events"].append({**ev_base, "outcome": "skipped_inside_or_after_window",
                                              "reason": "early mode never records checkpoint-window events"})
                    continue
            if acc.exists():
                prev = json.loads(acc.read_text(encoding="utf-8"))
                rec = seal({"schema": C.SCHEMA, "protocol": C.PROTOCOL, "status": "DUPLICATE_ATTEMPT", "mode": mode,
                            "run_id": run_id, **ev_base, "written_at": _iso_us(t_now),
                            "accepted_record": acc.relative_to(C.CAPTURE_DIR).as_posix(),
                            "accepted_record_sha256": prev.get("record_sha256")})
                write_attempt(run_id, f"{key}__{safe(cl)}__DUPLICATE_ATTEMPT", rec)
                summary["events"].append({**ev_base, "outcome": "DUPLICATE_ATTEMPT"})
                continue
            try:
                outcome = attempt(mode, comp, entry, groups, ctx, ctx_meta, cl, ev, comp_keys, clock, run_id, ev_base,
                                  proto_sha, start, cutoff, w_open, started, log)
            except Exception as exc:
                why = f"attempt error: {type(exc).__name__}: {exc}"
                diag(f"{key}__{safe(cl)}__FAILED_ATTEMPT", [why], {**ev_base})
                outcome = "FAILED_ATTEMPT"
            summary["events"].append({**ev_base, "outcome": outcome})
    return finish(0)


def attempt(mode, comp, entry, groups, ctx, ctx_meta, cl, ev, comp_keys, clock, run_id, ev_base, proto_sha,
            start, cutoff, w_open, started, log) -> str:
    key = comp.key
    reasons = list(ctx["problems"])
    t_pick = clock.t()
    cs = P.clusters(ctx)[cl]
    prov, _ = P.predict_frozen(comp, entry, ctx["events"], cs, t_pick)
    picks = P.market_picks(comp, entry, cs, prov, ctx["raw_by_ticker"], t_pick)
    candles, creqs, ckeys = {}, [], []
    tcan = D.Tracker(ev.get)
    for t in picks:
        c = next(x for x in cs if x["ticker"] == t)
        rts = int(clock.t())
        url, params = D.candle_request(c["series"], t, rts)
        raw = tcan.get("kalshi", url, params)
        if raw.status != 200:
            reasons.append(f"candle request HTTP {raw.status} for {t}")
        candles[t] = (raw, rts)
        creqs.append({"ticker": t, "series": c["series"], "request_ts": rts})
    ckeys = list(tcan.keys)
    as_of = clock.t()
    keys = comp_keys + [k for k in ckeys if k not in comp_keys]
    refs = ev.refs(keys)
    bad = [r for r in refs if r["status"] != 200]
    if bad:
        reasons.append(f"{len(bad)} evidence requests not HTTP 200")
    late = [r for r in refs if _ts(r["received_at"]) > as_of]
    if late:
        reasons.append(f"{len(late)} evidence retrievals completed after prediction_as_of")
    res = None
    if not reasons:
        res = compute_event(comp, entry, groups, ctx, cl, t_pick, as_of,
                            {t: candles[t] for t in picks if candles[t][0].status == 200})
        if res["market_picks"] != picks:
            reasons.append("market picks not reproducible from the recorded pick time")
        lr = res["as_of_view"]["latest_release_used"]
        if lr is not None and _ts(lr) > as_of:
            reasons.append("a result released after prediction_as_of was used")
    finalized = clock.t()
    if mode == "window":
        if not (w_open <= as_of <= finalized <= cutoff):
            reasons.append("timing: requires cutoff - 20 min <= prediction_as_of <= finalized_at <= cutoff")
    elif not finalized < w_open:
        reasons.append("timing: early snapshot finalized at or after the window opened")
    status = ("WINDOW_CAPTURE" if mode == "window" else "EARLY_SNAPSHOT") if not reasons else "FAILED_ATTEMPT"
    rec = {"schema": C.SCHEMA, "protocol": C.PROTOCOL, "status": status, "mode": mode, "run_id": run_id, **ev_base,
           "sport": comp.sport, "timing_label": ("WINDOW_CAPTURE: actual horizon recorded; not an exact T-60 "
                                                 "observation" if mode == "window" else
                                                 "EARLY_SNAPSHOT: development record before the window"),
           "comparability": C.ADAPTERS[comp.source]["comparability"],
           "comparability_note": C.COMPARABILITY[C.ADAPTERS[comp.source]["comparability"]],
           "capture_started_at": _iso_us(started.timestamp()), "market_pick_time": _iso_us(t_pick),
           "market_pick_ts": t_pick, "prediction_as_of": _iso_us(as_of), "prediction_as_of_ts": as_of,
           "finalized_at": _iso_us(finalized), "horizon_minutes": round((start - as_of) / 60, 3),
           "model": {"phase1_protocol": C.PHASE1_PROTOCOL, "params": entry["params"],
                     "primary_rule": "phase2.folds.primary_row"},
           "context": ctx_meta, "candle_requests": creqs, "evidence": refs,
           "evidence_root": _relpath(C.EVIDENCE_ROOT / run_id),
           "dependencies": PN.dependency_hashes(comp), "pins": PN.global_pins(),
           "capture_protocol_sha256": proto_sha, "research_only": True, "no_bet": True}
    if res is not None:
        rec.update(res)
    if reasons:
        rec["failure_reasons"] = reasons
        rec = seal(rec)
        write_attempt(run_id, f"{key}__{safe(cl)}__FAILED_ATTEMPT", rec)
        log(f"{key} {cl}: FAILED_ATTEMPT {reasons[:3]}")
        return "FAILED_ATTEMPT"
    rec = seal(rec)
    acc = accepted_path(mode, key, cl)
    if not write_accepted(acc, rec):
        prev = json.loads(acc.read_text(encoding="utf-8"))
        dup = seal({**{k: v for k, v in rec.items() if k != "record_sha256"}, "status": "DUPLICATE_ATTEMPT",
                    "accepted_record": acc.relative_to(C.CAPTURE_DIR).as_posix(),
                    "accepted_record_sha256": prev.get("record_sha256")})
        write_attempt(run_id, f"{key}__{safe(cl)}__DUPLICATE_ATTEMPT", dup)
        return "DUPLICATE_ATTEMPT"
    n = {k: sum(1 for c in rec["contracts"] if "p" in c["candidates"][k]) for k in P.CANDIDATES}
    log(f"{key} {cl}: {status} horizon={rec['horizon_minutes']} min; contracts={len(rec['contracts'])} "
        f"with p: {n}")
    return status


def upcoming(log, hours: float = 24.0, session=None) -> int:
    """Network: milestones only. Lists when checkpoint windows open for supported competitions."""
    import tempfile
    by_key, series_spec, series_comp = _comp_maps()
    with tempfile.TemporaryDirectory() as tmp:
        ev = D.Evidence(Path(tmp), lambda: datetime.now(timezone.utc), session)
        rows, st = D.milestones(ev.get)
    now = datetime.now(timezone.utc).timestamp()
    out = []
    for m in rows:
        if not m.get("start") or not (now <= _ts(m["start"]) <= now + hours * 3600):
            continue
        ks = sorted({series_comp[et.split("-")[0]] for et in m["event_tickers"] if et.split("-")[0] in series_comp})
        for k in ks:
            s = _ts(m["start"])
            out.append((s - (C.HORIZON_MIN + C.WINDOW_MIN) * 60, k, by_key[k].source, m["title"], m["start"]))
    for w, k, src, title, s in sorted(out):
        sup = C.ADAPTERS[src]["status"]
        log(f"window opens {_iso(w)} (Kalshi start {s}; source start may differ)  {k:<22} {sup:<11} {title}")
    log(f"{len(out)} supported-series events in the next {hours} h; discovery complete={st['complete']}")
    return 0 if st["complete"] else 2
