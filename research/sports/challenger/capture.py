"""Standalone manual capture for the frozen ITF singles Elo challenger.

Not connected to the scheduler. Does not write live capture_v2 accepted records.
Does not edit ``itf_singles_v1.predict`` or the challenger study protocol.

    python -m research.sports.challenger.capture freeze
    python -m research.sports.challenger.capture verify
    python -m research.sports.challenger.capture upcoming [--hours 24]
    python -m research.sports.challenger.capture dry-run
    python -m research.sports.challenger.capture window
    python -m research.sports.challenger.capture settle
    python -m research.sports.challenger.capture report

Dry-run writes no prediction, exclusion or settlement records. Discovery still uses
the byte-exact evidence cache under data/cache/sports/raw_challenger_itf_v1/.
RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from ..benchmark import quote_at_cutoff
from ..competitions import all_competitions
from ..core import REPO_ROOT, sha256_bytes, sha256_file, utc_now_iso
from ..live import config as LC
from ..live import discover as D
from ..live import pins as PN
from ..live import predict as LP
from ..live import sources as S
from ..phase2.market import listing_ts, pick_contracts
from ..scoring import settle as ST
from . import history as H
from . import itf_singles_v1 as CH
from . import records as R
from . import report as RP
from .constants import (
    CAPTURE_CODE_FILES, CAPTURE_DIR, CAPTURE_PROTOCOL, CAPTURE_PROTOCOL_PATH, CHALLENGER_DIR,
    COMPETITIONS, EVIDENCE_ROOT, NOMINAL_HORIZON_MIN, PAIRED_FROZEN_PARAMS, PROTOCOL_PATH,
    REPORT_CODE_FILES,
)


def capture_code_hash() -> str:
    return sha256_bytes(b"".join(sha256_file(CHALLENGER_DIR / f).encode() for f in CAPTURE_CODE_FILES))


def report_code_hash() -> str:
    return sha256_bytes(b"".join(sha256_file(CHALLENGER_DIR / f).encode() for f in REPORT_CODE_FILES))


def _iso_us(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


class Clock:
    def __init__(self, now_fn):
        self.now_fn = now_fn

    def now(self) -> datetime:
        return self.now_fn()

    def t(self) -> float:
        return self.now_fn().timestamp()


def log(msg: str) -> None:
    print(f"[{utc_now_iso()}] {msg}", flush=True)


def itf_competitions():
    comps, specs, _ = all_competitions()
    by_key = {c.key: c for c in comps}
    series_spec = {k: {s: (kind, seg) for s, kind, seg in v} for k, v in specs.items()}
    series_comp = {s: k for k, v in series_spec.items() for s in v}
    wanted = [by_key[k] for k in COMPETITIONS if k in by_key]
    return wanted, by_key, series_spec, series_comp


def challenger_params() -> dict:
    return dict(CH.load_protocol()["selected"]["params"])


def global_capture_pins() -> dict:
    g = PN.global_pins()
    proto = CH.load_protocol()
    return {
        "phase1_code_hash": g["phase1_code_hash"],
        "phase2_code_hash": g["phase2_code_hash"],
        "live_code_hash": g["live_code_hash"],
        "phase1_protocol_sha256": g["phase1_protocol_sha256"],
        "phase2_protocol_sha256": g["phase2_protocol_sha256"],
        "challenger_code_hash": CH.code_hash(),
        "challenger_protocol_sha256": sha256_file(PROTOCOL_PATH),
        "capture_code_hash": capture_code_hash(),
        "report_code_hash": report_code_hash(),
        "challenger_frozen_at": proto["frozen_at"],
        "events_sha256": proto["pins"]["events_sha256"],
        "contracts_sha256": proto["pins"]["contracts_sha256"],
    }


def freeze_capture_protocol(log_fn=log) -> dict:
    """Write the capture-wrapper protocol once. Does not touch the frozen helper protocol."""
    if not PROTOCOL_PATH.is_file():
        raise SystemExit("challenger protocol missing; refusing to freeze capture wrapper")
    body = {
        "protocol": CAPTURE_PROTOCOL,
        "parent_challenger_protocol": "sports_itf_singles_challenger_v1",
        "research_only": True,
        "no_bet": True,
        "scheduler": "none",
        "competitions": list(COMPETITIONS),
        "capture_code_files": list(CAPTURE_CODE_FILES),
        "report_code_files": list(REPORT_CODE_FILES),
        "rules": {
            "window": "start-80min <= prediction_as_of <= recorded_at <= start-60min",
            "prediction_as_of": "frozen after bounded evidence fetches complete",
            "recorded_at": "clock at persist; never backdated",
            "horizons": {
                "horizon_minutes": "(start - prediction_as_of) / 60",
                "recorded_horizon_minutes": "(start - recorded_at) / 60",
                "nominal_horizon_minutes": NOMINAL_HORIZON_MIN,
            },
            "helper": "CH.predict(events, queries, params) unchanged; queries are "
                      "(key, participant_uuid, opponent_uuid, start_timestamp)",
            "as_of_history": "only results released and locally received by prediction_as_of; "
                             "separate men/women pools; helper refused if a later release remains",
            "market": "quote_at_cutoff at prediction_as_of; existing two-sided hourly candle rule",
            "dry_run": "no prediction/exclusion/settlement records; evidence cache may still be written",
        },
        "pins": global_capture_pins(),
        "candidate_id": CH.CANDIDATE_ID,
        "paired_frozen_params": PAIRED_FROZEN_PARAMS,
        "challenger_params": challenger_params(),
    }
    path = CAPTURE_PROTOCOL_PATH
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "frozen_at"} != json.loads(json.dumps(body)):
            raise SystemExit(f"{path.name} is frozen and the recomputed content differs")
        log_fn(f"{path.name} unchanged")
        return old
    body["frozen_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    path.write_text(json.dumps(body, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    log_fn(f"capture protocol frozen: {path}")
    return body


def load_capture_protocol() -> dict:
    if not CAPTURE_PROTOCOL_PATH.is_file():
        raise SystemExit(f"{CAPTURE_PROTOCOL_PATH.name} not frozen; "
                         "run `python -m research.sports.challenger.capture freeze` first")
    return json.loads(CAPTURE_PROTOCOL_PATH.read_text(encoding="utf-8"))


def check_pins(proto: dict | None = None) -> list[str]:
    proto = proto or load_capture_protocol()
    problems = []
    have = global_capture_pins()
    want = proto.get("pins") or {}
    for k, v in sorted(want.items()):
        if have.get(k) != v:
            problems.append(f"{k} differs from {CAPTURE_PROTOCOL}")
    ch = CH.load_protocol()
    if ch["challenger_code_hash"] != CH.code_hash():
        problems.append("frozen helper code hash changed")
    if have.get("challenger_code_hash") and have.get("challenger_code_hash") != ch["challenger_code_hash"]:
        problems.append("challenger_code_hash does not match frozen helper protocol")
    comps, _, _, _ = itf_competitions()
    live_proto = PN.load_protocol()
    problems.extend(PN.check(live_proto, comps))
    return problems


def verify(log_fn=log) -> int:
    try:
        proto = load_capture_protocol()
    except SystemExit as exc:
        log_fn(str(exc))
        return 2
    problems = check_pins(proto)
    for p in problems:
        log_fn(f"PIN PROBLEM: {p}")
    log_fn(f"verify {CAPTURE_PROTOCOL}: {'OK' if not problems else f'{len(problems)} problems'}")
    return 0 if not problems else 1


def upcoming(log_fn=log, hours: float = 24.0, session=None) -> int:
    import tempfile
    wanted, by_key, _, series_comp = itf_competitions()
    keys = {c.key for c in wanted}
    with tempfile.TemporaryDirectory() as tmp:
        ev = D.Evidence(Path(tmp), lambda: datetime.now(timezone.utc), session)
        rows, st = D.milestones(ev.get)
    now = datetime.now(timezone.utc).timestamp()
    out = []
    for m in rows:
        if not m.get("start") or not (now <= _ts(m["start"]) <= now + hours * 3600):
            continue
        ks = sorted({series_comp[et.split("-")[0]] for et in m["event_tickers"]
                     if et.split("-")[0] in series_comp and series_comp[et.split("-")[0]] in keys})
        for k in ks:
            s = _ts(m["start"])
            out.append((s - (LC.HORIZON_MIN + LC.WINDOW_MIN) * 60, k, m["title"], m["start"]))
    for w, k, title, s in sorted(out):
        log_fn(f"window opens {_iso(w)} (Kalshi start {s})  {k:<22} {title}")
    log_fn(f"{len(out)} ITF events in the next {hours} h; discovery complete={st['complete']}")
    if not out:
        log_fn("next command: python -m research.sports.challenger.capture upcoming")
        log_fn("when a window is open: python -m research.sports.challenger.capture window")
    return 0 if st["complete"] else 2


def _window_bounds(start: float) -> tuple[float, float]:
    cutoff = start - LC.HORIZON_MIN * 60
    return cutoff - LC.WINDOW_MIN * 60, cutoff


def live_frozen_lookup(comp_key: str, cluster: str, ticker: str) -> tuple[float | None, str]:
    path = LC.CAPTURE_DIR / "accepted" / "checkpoint" / comp_key / f"{R.safe(cluster)}.json"
    if not path.is_file():
        return None, "no live capture_v2 accepted record"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "live capture_v2 record unreadable"
    for c in rec.get("contracts") or []:
        if c.get("ticker") != ticker:
            continue
        p = ((c.get("candidates") or {}).get("frozen") or {}).get("p")
        if isinstance(p, (int, float)):
            return float(p), "ok"
        return None, "live frozen p unavailable"
    return None, "ticker not on live capture_v2 record"


def _quote(candles_raw, as_of: float, request_ts: int) -> dict:
    if candles_raw is None:
        return {"unavailable": "candle request missing"}
    if candles_raw.status != 200:
        return {"unavailable": f"candle HTTP {candles_raw.status}"}
    if math.floor(request_ts / 3600) != math.floor(as_of / 3600):
        return {"unavailable": "an hour boundary passed between the candle request and prediction_as_of"}
    try:
        sticks = candles_raw.json().get("candlesticks") or []
    except (ValueError, UnicodeDecodeError):
        return {"unavailable": "unparseable candlesticks"}
    q = quote_at_cutoff(sticks, int(as_of))
    if q["status"] != "ok":
        return {"unavailable": f"market quote {q['status']}",
                **{k: q[k] for k in ("candle_end", "age_s") if k in q}}
    return {"p": q["mid"], "bid": q["bid"], "ask": q["ask"], "candle_end": q["candle_end"], "age_s": q["age_s"]}


def run_window(*, dry_run: bool, log_fn=log, now_fn=None, session=None) -> int:
    clock = Clock(now_fn or (lambda: datetime.now(timezone.utc)))
    started = clock.now()
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
    mode = "dry-run" if dry_run else "window"
    try:
        cap_proto = load_capture_protocol()
    except SystemExit as exc:
        log_fn(str(exc))
        return 2
    pin_problems = check_pins(cap_proto)
    if pin_problems:
        for p in pin_problems:
            log_fn(f"PIN PROBLEM: {p}")
        return 2
    wanted, by_key, series_spec, series_comp = itf_competitions()
    if len(wanted) != 2:
        log_fn(f"expected both ITF competitions, found {[c.key for c in wanted]}")
        return 2
    evidence_root = EVIDENCE_ROOT / ("dry-run" if dry_run else "window") / run_id
    ev = D.Evidence(evidence_root, clock.now, session)
    tr = D.Tracker(ev.get)
    ms_rows, st = D.milestones(tr.get)
    if not st["complete"]:
        log_fn(f"milestone discovery incomplete: {st['error']}")
        return 2
    now = clock.t()
    lo, hi = now + LC.WINDOW_DISCOVERY_H[0] * 3600, now + LC.WINDOW_DISCOVERY_H[1] * 3600
    per_comp: dict[str, set] = {}
    latest: dict[str, float] = {}
    for m in ms_rows:
        if not m.get("start") or not (lo <= _ts(m["start"]) <= hi):
            continue
        for et in m["event_tickers"]:
            k = series_comp.get(et.split("-")[0])
            if k in COMPETITIONS:
                per_comp.setdefault(k, set()).add(et)
                latest[k] = max(latest.get(k, 0.0), _ts(m["start"]))
    log_fn(f"discovery: {len(ms_rows)} milestones; "
           f"{sum(len(v) for v in per_comp.values())} ITF events in "
           f"{sorted(per_comp)} within [{_iso(lo)}, {_iso(hi)}]")
    log_fn(f"evidence cache writes: {_rel(evidence_root)} "
           "(byte-exact Fetcher cache). Dry-run writes no prediction, exclusion or settlement records."
           if dry_run else
           f"evidence cache writes: {_rel(evidence_root)} (byte-exact Fetcher cache).")
    in_window = 0
    captured = 0
    excluded_n = 0
    for key in COMPETITIONS:
        if key not in per_comp:
            continue
        comp = by_key[key]
        history_end = _iso(max(clock.t(), latest[key]) + 86400)
        tc = D.Tracker(ev.get)
        try:
            ctx = LP.build_context(comp, tc.get, sorted(per_comp[key]), history_end, series_spec[key])
        except Exception as exc:
            log_fn(f"{key}: context build error: {type(exc).__name__}: {exc}")
            continue
        if ctx["problems"]:
            log_fn(f"{key}: context problems {ctx['problems'][:3]}")
        frozen_receipt = _iso_us(clock.t())
        settled_receipts = [r["received_at"] for r in ev.refs(tc.keys)
                            if (r.get("params") or {}).get("status") == "settled"]
        live_receipt = max(settled_receipts) if settled_receipts else frozen_receipt
        frozen_by = {e["source_game_id"]: e for e in S.frozen_events(comp)}
        annotated = []
        for e in ctx["events"]:
            prev = frozen_by.get(e["source_game_id"])
            if (prev is not None and prev.get("available_at") == e.get("available_at")
                    and prev.get("winner_id") == e.get("winner_id")):
                annotated.append(H.annotate([e], frozen_receipt, "frozen_normalized")[0])
            else:
                annotated.append(H.annotate([e], live_receipt, "live_settled_markets")[0])
        by_cluster = LP.clusters(ctx)
        for cl in sorted(by_cluster, key=lambda c: (min(_ts(x["start"]) for x in by_cluster[c]), c)):
            cs = by_cluster[cl]
            start = min(_ts(x["start"]) for x in cs)
            w_open, cutoff = _window_bounds(start)
            t_now = clock.t()
            if t_now < w_open or t_now > cutoff:
                continue
            in_window += 1
            outcome = _attempt(mode, dry_run, comp, ctx, annotated, cl, cs, start, w_open, cutoff,
                               ev, list(tc.keys), clock, run_id, cap_proto, log_fn)
            if outcome == "PREDICTION":
                captured += 1
            elif outcome == "EXCLUSION":
                excluded_n += 1
    if in_window == 0:
        log_fn("no eligible ITF event is inside the 60-80 minute capture window")
        log_fn("exact next commands:")
        log_fn("  python -m research.sports.challenger.capture upcoming")
        log_fn("  python -m research.sports.challenger.capture window")
        upcoming(log_fn, hours=24.0, session=session)
        return 0
    log_fn(f"{mode}: in_window={in_window} predictions={captured} exclusions={excluded_n} "
           f"dry_run={dry_run}")
    return 0


def _attempt(mode, dry_run, comp, ctx, annotated, cl, cs, start, w_open, cutoff,
             ev, comp_keys, clock, run_id, cap_proto, log_fn) -> str:
    key = comp.key
    pred_path = R.prediction_path(key, cl)
    existing, estatus = R.inspect_record(pred_path)
    if estatus == "ok":
        log_fn(f"{key} {cl}: duplicate complete prediction exists; not overwritten")
        return "DUPLICATE"
    if estatus in ("incomplete", "checksum_mismatch"):
        log_fn(f"{key} {cl}: {estatus} prediction file at {pred_path}; refusing overwrite")
        return "INCOMPLETE"
    reasons = list(ctx.get("problems") or [])
    ok_cs = [c for c in cs if c.get("status") == "ok"]
    tcan = D.Tracker(ev.get)
    candles, creqs = {}, []
    for c in ok_cs:
        rts = int(clock.t())
        url, params = D.candle_request(c["series"], c["ticker"], rts)
        raw = tcan.get("kalshi", url, params)
        candles[c["ticker"]] = (raw, rts)
        creqs.append({"ticker": c["ticker"], "series": c["series"], "request_ts": rts})
    as_of = clock.t()
    keys = list(dict.fromkeys(comp_keys + list(tcan.keys)))
    refs = ev.refs(keys)
    bad = [r for r in refs if r["status"] != 200]
    if bad:
        reasons.append(f"{len(bad)} evidence requests not HTTP 200")
    late = [r for r in refs if _ts(r["received_at"]) > as_of]
    if late:
        reasons.append(f"{len(late)} evidence retrievals completed after prediction_as_of")
    view, vstats = H.as_of_history(comp, annotated, as_of, start)
    if view is None:
        reasons.append(vstats.get("unavailable") or "as_of_history unavailable")
    queries, qwhy = H.queries_for_contracts(ok_cs, start)
    if qwhy:
        reasons.append(qwhy)
    p_ch = p_fz = {}
    if view is not None and queries and not reasons:
        p_ch, p_fz = H.predict_paired(view, queries, challenger_params())
    listed = []
    for c in ok_cs:
        lt = listing_ts(ctx["raw_by_ticker"].get(c["ticker"]) or {})
        if lt is not None and lt <= as_of:
            listed.append(c)
    picks = [c["ticker"] for c in pick_contracts("game_winner", listed)] if listed else []
    quotes = {t: _quote(candles[t][0], as_of, candles[t][1]) if t in candles else
              {"unavailable": "candle request missing"} for t in picks}
    outcome_av = H.capture_time_outcome(ok_cs, ctx["raw_by_ticker"], view or [], cl, as_of)
    contracts_out = []
    qmap = {q[0]: q for q in queries}
    for c in sorted(cs, key=lambda x: x["ticker"]):
        t = c["ticker"]
        q = qmap.get(t)
        direction = None
        if q:
            direction = {"yes": "named_participant", "participant": q[1], "opponent": q[2]}
        ch = ({"p": p_ch[t]} if t in p_ch else
              {"unavailable": (vstats.get("unavailable") if view is None else "no challenger p")})
        fz = ({"p": p_fz[t]} if t in p_fz else
              {"unavailable": (vstats.get("unavailable") if view is None else "no paired frozen p")})
        live_p, live_why = live_frozen_lookup(key, cl, t)
        live_match = None
        if live_p is not None and "p" in fz:
            live_match = abs(live_p - fz["p"]) <= 1e-12
        mk = quotes.get(t) if t in picks else {"unavailable": "not selected by the frozen per-event contract rule"}
        contracts_out.append({
            "ticker": t, "event_ticker": c.get("event_ticker"), "series": c.get("series"),
            "family": c.get("family"), "status": c.get("status"),
            "reason": c.get("reason"), "participant": c.get("participant"),
            "opponent": q[2] if q else None, "contract_direction": direction,
            "challenger": ch, "paired_frozen": fz,
            "live_frozen_p": live_p, "live_frozen_note": live_why,
            "live_frozen_matches_paired": live_match,
            "market": mk, "market_pick": t in picks,
        })
    recorded_at = clock.t()
    timing_ok = w_open <= as_of <= recorded_at <= cutoff
    if not timing_ok:
        reasons.append(
            "timing: persist clock is outside [start-80min, start-60min]; recorded_at was not backdated"
        )
    rec = {
        "schema": R.SCHEMA_PREDICTION if not reasons else R.SCHEMA_EXCLUSION,
        "protocol": CAPTURE_PROTOCOL,
        "parent_challenger_protocol": "sports_itf_singles_challenger_v1",
        "candidate_id": CH.CANDIDATE_ID,
        "status": "EXCLUDED" if reasons else "PREDICTION",
        "mode": mode,
        "dry_run": dry_run,
        "run_id": run_id,
        "competition": key,
        "cluster": cl,
        "start": _iso(start),
        "scheduled_cutoff": _iso(cutoff),
        "window_open": _iso(w_open),
        "prediction_as_of": _iso_us(as_of),
        "recorded_at": _iso_us(recorded_at),
        "horizon_minutes": (start - as_of) / 60.0,
        "recorded_horizon_minutes": (start - recorded_at) / 60.0,
        "nominal_horizon_minutes": NOMINAL_HORIZON_MIN,
        "challenger_code_hash": cap_proto["pins"]["challenger_code_hash"],
        "capture_code_hash": cap_proto["pins"]["capture_code_hash"],
        "protocol_hash": sha256_file(CAPTURE_PROTOCOL_PATH),
        "challenger_protocol_sha256": cap_proto["pins"]["challenger_protocol_sha256"],
        "imported_dependency_hashes": PN.dependency_hashes(comp),
        "pins": cap_proto["pins"],
        "as_of_view": vstats,
        "history_counts": {"pool": key, "n_input": vstats.get("n_input"),
                           "results_used": vstats.get("results_used"),
                           "events_hidden": vstats.get("events_hidden")},
        "evidence": refs,
        "evidence_root": _rel(ev.root),
        "candle_requests": creqs,
        "outcome_availability": outcome_av,
        "contracts": contracts_out,
        "research_only": True,
        "no_bet": True,
    }
    if reasons:
        rec["reason"] = reasons[0]
        rec["failure_reasons"] = reasons
    if dry_run:
        log_fn(f"{key} {cl}: dry-run would {'exclude' if reasons else 'record'} "
               f"horizon_minutes={rec['horizon_minutes']:.3f} "
               f"(not written; evidence cache may contain bytes under {_rel(ev.root)})")
        return "DRY_RUN"
    try:
        if reasons:
            path = R.exclusion_path(key, cl, run_id)
            R.publish_exclusive(path, rec)
            log_fn(f"{key} {cl}: EXCLUSION {reasons[0]}")
            return "EXCLUSION"
        R.publish_exclusive(pred_path, rec)
        log_fn(f"{key} {cl}: PREDICTION horizon_minutes={rec['horizon_minutes']:.3f} "
               f"recorded_horizon_minutes={rec['recorded_horizon_minutes']:.3f} "
               f"contracts={len(contracts_out)}")
        return "PREDICTION"
    except R.RecordExists:
        log_fn(f"{key} {cl}: duplicate; existing complete record kept")
        return "DUPLICATE"
    except R.IncompleteRecord as exc:
        log_fn(f"{key} {cl}: {exc}")
        return "INCOMPLETE"


def settle(log_fn=log, now_fn=None, session=None) -> int:
    clock = Clock(now_fn or (lambda: datetime.now(timezone.utc)))
    run_id = clock.now().strftime("%Y%m%dT%H%M%S%fZ")
    try:
        cap_proto = load_capture_protocol()
    except SystemExit as exc:
        log_fn(str(exc))
        return 2
    problems = check_pins(cap_proto)
    if problems:
        for p in problems:
            log_fn(f"PIN PROBLEM: {p}")
        return 2
    ev = D.Evidence(EVIDENCE_ROOT / "settle" / run_id, clock.now, session)
    n = 0
    for path, pred in R.list_complete(CAPTURE_DIR / "predictions"):
        dest = R.settlement_path(pred["competition"], pred["cluster"])
        existing, st = R.inspect_record(dest)
        if st == "ok":
            continue
        if st in ("incomplete", "checksum_mismatch"):
            log_fn(f"{dest}: {st}; refusing overwrite")
            continue
        tickers = [c["ticker"] for c in pred.get("contracts") or []]
        ets = sorted({c.get("event_ticker") for c in pred.get("contracts") or [] if c.get("event_ticker")})
        if not ets:
            ets = sorted({(c.get("ticker") or "").rsplit("-", 1)[0]
                          for c in pred.get("contracts") or [] if c.get("ticker")})
        found, failed = ST.lookup(ev.get, ets, tickers)
        rows = []
        for t in tickers:
            info = found.get(t) or {"state": "LOOKUP_FAILED", "detail": "not in lookup"}
            y = None
            if info.get("state") == "SETTLED_YES":
                y = 1
            elif info.get("state") == "SETTLED_NO":
                y = 0
            rows.append({"ticker": t, **{k: info.get(k) for k in ("state", "detail", "market")}, "y": y})
        rec = {
            "schema": R.SCHEMA_SETTLEMENT,
            "protocol": CAPTURE_PROTOCOL,
            "competition": pred["competition"],
            "cluster": pred["cluster"],
            "prediction_record_sha256": pred.get("record_sha256"),
            "settled_at": _iso_us(clock.t()),
            "contracts": rows,
            "lookup_failed": failed,
            "note": "Settlement does not prove the outcome was unavailable at prediction time. "
                    "Prospective eligibility uses capture-time outcome_availability only.",
            "research_only": True,
            "no_bet": True,
        }
        try:
            R.publish_exclusive(dest, rec)
            n += 1
            log_fn(f"{pred['competition']} {pred['cluster']}: settlement written")
        except (R.RecordExists, R.IncompleteRecord) as exc:
            log_fn(str(exc))
    log_fn(f"settle: wrote {n} settlement records; evidence cache {_rel(ev.root)}")
    return 0


def report(log_fn=log) -> int:
    summary = RP.summarize()
    out = CAPTURE_DIR / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log_fn(RP.render(summary).rstrip())
    log_fn(f"report json { _rel(out) } (runtime; not committed)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.sports.challenger.capture")
    p.add_argument("stage", choices=["freeze", "verify", "upcoming", "dry-run", "window", "settle", "report"])
    p.add_argument("--hours", type=float, default=24.0)
    a = p.parse_args(argv)
    if a.stage == "freeze":
        freeze_capture_protocol(log)
        return 0
    if a.stage == "verify":
        return verify(log)
    if a.stage == "upcoming":
        return upcoming(log, a.hours)
    if a.stage == "dry-run":
        return run_window(dry_run=True, log_fn=log)
    if a.stage == "window":
        return run_window(dry_run=False, log_fn=log)
    if a.stage == "settle":
        return settle(log)
    return report(log)


if __name__ == "__main__":
    raise SystemExit(main())
