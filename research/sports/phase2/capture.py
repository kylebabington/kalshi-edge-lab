"""Manual prospective capture (RESEARCH_ONLY / NO_BET; never scheduled).

Two modes, kept apart:

* ``early``  -> EARLY_SNAPSHOT records: development/smoke forecasts for events whose T-60 cutoff is
  still in the future, labelled with their actual horizon. Never treated as checkpoint forecasts.
* ``checkpoint`` -> only events whose capture window [cutoff - 20 min, cutoff] contains the capture
  start become CHECKPOINT records, and only if every piece of evidence was retrieved at or before
  the evidence cutoff (= scheduled cutoff); otherwise MISSED. Events whose cutoff already passed
  are recorded as MISSED; nothing is ever backfilled.

Evidence (ESPN scoreboards, Kalshi open game-winner markets) goes to a fresh per-run raw cache.
``capture-replay`` recomputes a record offline from that cache with the same code path.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import adapters as ad
from .. import evaluate as EV
from .. import models as M
from ..competitions import all_competitions
from ..core import CROSSWALK, NORMALIZED, CacheMiss, Fetcher, iso, read_jsonl, sha256_file, sha256_json, utc_now_iso
from ..engines import Elo, replay as replay_releases
from . import config as C

SCHEMA = "sports_phase2_capture_v1"
DEFAULT_COMPS = ["nba", "wnba", "nfl", "mlb", "nhl"]
MAX_SCOREBOARD_DAYS = 14


def _ts(s: str) -> float:
    return M.ts(s)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_from_game(g: dict, comp) -> dict:
    h, a = g["home"], g["away"]
    winner = None
    if g["completed"] and h["score"] is not None and a["score"] is not None:
        if h["winner"] and not a["winner"]:
            winner = "home"
        elif a["winner"] and not h["winner"]:
            winner = "away"
        else:
            winner = "tie" if h["score"] == a["score"] else ("home" if h["score"] > a["score"] else "away")
    return {"source_game_id": f"espn:{g['espn_id']}", "start": g["start"], "completed": g["completed"],
            "neutral": g["neutral"], "season_type": g["season_type"], "home": f"espn:{h['espn_team_id']}",
            "away": f"espn:{a['espn_team_id']}", "home_score": h["score"], "away_score": a["score"], "winner": winner,
            "home_periods": h["periods"], "away_periods": a["periods"], "status": g["status"]}


def evidence_requests(comp, hist_events, gw_series, day0: datetime) -> list[tuple[str, str, dict]]:
    """(source, url, params) for one capture: scoreboards since the frozen history ends, open GW markets."""
    done = [_ts(e["start"]) for e in hist_events if e.get("completed")]
    first = datetime.fromtimestamp(max(done), timezone.utc).date() if done else day0.date()
    first = max(first, (day0 - timedelta(days=MAX_SCOREBOARD_DAYS)).date())
    reqs = [("espn", u, p) for u, p in ad.espn_requests(comp.espn_path, "day", first, (day0 + timedelta(days=1)).date(),
                                                         None, None, None, comp.espn_limit)]
    for s in sorted(gw_series):
        reqs.append(("kalshi", f"{ad.KALSHI_BASE}/markets", {"series_ticker": s, "status": "open", "limit": 1000}))
    return reqs


def _abbr_map(comp) -> dict:
    """Kalshi team abbreviation -> ESPN team id (via structured targets and the Phase 1 crosswalk)."""
    cw = json.loads((CROSSWALK / f"{comp.key}_participants.json").read_text(encoding="utf-8"))["mapping"]
    targets = ad.load_structured_targets()
    out, dup = {}, set()
    for kid, espn in cw.items():
        ab = ((targets.get(kid) or {}).get("abbreviation") or "").upper()
        if not ab:
            continue
        if ab in out and out[ab] != espn:
            dup.add(ab)
        out[ab] = espn
    return {k: v for k, v in out.items() if k not in dup}


def _ticker_date(event_ticker: str):
    """Game date (US Eastern) encoded in a Kalshi event ticker, e.g. KXNBAGAME-26OCT20BOSDET -> 2026-10-20."""
    try:
        return datetime.strptime(event_ticker.split("-")[1][:7], "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def gw_series_of(comp) -> list[str]:
    return sorted({c["series"] for c in read_jsonl(NORMALIZED / comp.key / "contracts.jsonl")
                   if c["family"] == "game_winner" and not c.get("segment")})


def compute(comp, params: dict, run_root: Path, reqs, as_of_fn) -> tuple[list[dict], list[dict]]:
    """Forecasts from frozen history + saved evidence (cache-only); identical for capture and replay."""
    f = Fetcher(cache_only=True, root=run_root)
    hist = list(read_jsonl(NORMALIZED / comp.key / "events.jsonl"))
    by_id = {e["source_game_id"]: e for e in hist}
    markets, ev_meta = [], []
    for src, url, prm in reqs:
        raw = f.get(src, url, prm)
        ev_meta.append({"source": src, "url": url, "params": prm, "status": raw.status, "sha256": raw.meta["sha256"],
                        "retrieved_at": raw.meta["retrieved_at"]})
        if src == "espn":
            for g in ad.parse_espn_team_games(raw, comp.espn_path):
                e = _event_from_game(g, comp)
                prev = by_id.get(e["source_game_id"])
                if prev is None or not prev.get("completed"):
                    by_id[e["source_game_id"]] = {**(prev or {}), **e}
        elif raw.ok:
            markets += raw.json().get("markets") or []
    events = sorted(by_id.values(), key=lambda e: (e["start"], e["source_game_id"]))
    abbr = _abbr_map(comp)
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], []).append(m)
    out = []
    for et, ms in sorted(by_event.items()):
        teams = {}
        for m in ms:
            ab = m["ticker"].rsplit("-", 1)[-1].upper()
            if ab in abbr:
                teams[abbr[ab]] = m
        if len(teams) != 2:
            out.append({"kalshi_event_ticker": et, "skip": "participants not resolved through the crosswalk"})
            continue
        ids = set(teams)
        games = [e for e in events if {e.get("home"), e.get("away")} == ids and not e.get("completed")
                 and e.get("season_type") not in comp.exclude_season_types]
        if len(games) > 1:
            day = _ticker_date(et)
            games = [e for e in games if day is not None and
                     day in {datetime.fromtimestamp(_ts(e["start"]) - h * 3600, timezone.utc).date() for h in (4, 5)}]
        if len(games) != 1:
            out.append({"kalshi_event_ticker": et, "skip": f"{len(games)} matching scheduled ESPN games"})
            continue
        g = games[0]
        start = _ts(g["start"])
        as_of = as_of_fn(start)
        rel = M.team_releases(comp, [e for e in events if e.get("completed")])
        elo = Elo(params["k"], params["home"], params["carry"], params["mov"])
        res = {}

        def upd(e):
            s = 1.0 if e["winner"] == "home" else 0.0 if e["winner"] == "away" else 0.5
            elo.update(e["home"], e["away"], _ts(e["start"]), s, e["neutral"], e["home_score"] - e["away_score"])

        def pred(q):
            res["p_home"] = elo.prob(g["home"], g["away"], start, g["neutral"])

        replay_releases(rel, [(as_of, None)], upd, pred)
        hm = teams[g["home"]]
        bid, ask = ad._num(hm.get("yes_bid_dollars")), ad._num(hm.get("yes_ask_dollars"))
        mkt = {"ticker": hm["ticker"], "yes_bid": bid, "yes_ask": ask,
               "mid": (bid + ask) / 2 if bid is not None and ask is not None and 0 < bid <= ask < 1 else None}
        out.append({"kalshi_event_ticker": et, "source_game_id": g["source_game_id"], "home": g["home"],
                    "away": g["away"], "start": g["start"], "as_of": _iso(as_of), "p_home": res["p_home"],
                    "market_home": mkt})
    return out, ev_meta


def run_capture(mode: str, competitions: str | None, log, now_fn=None) -> int:
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    started = now_fn()
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
    run_root = C.CAPTURE_CACHE / run_id
    keys = [k.strip() for k in (competitions or ",".join(DEFAULT_COMPS)).split(",") if k.strip()]
    proto1 = EV.load_protocol(C.PHASE1_PROTOCOL)
    comps = {c.key: c for c in all_competitions()[0]}
    net = Fetcher(root=run_root, min_interval=C.BUDGET["min_interval_s"], max_retries=2)
    win = C.CAPTURE["window_minutes_before_cutoff"] * 60
    written = 0
    for key in keys:
        comp = comps.get(key)
        entry = (proto1["competitions"].get(key) or {})
        elo_p = (entry.get("params") or {}).get("elo")
        if comp is None or comp.source != "espn_team" or not elo_p:
            log(f"capture {key}: unavailable (scope is espn_team two-way game winners with a frozen Elo model)")
            continue
        hist = list(read_jsonl(NORMALIZED / key / "events.jsonl"))
        reqs = evidence_requests(comp, hist, gw_series_of(comp), started)
        for src, url, prm in reqs:
            net.get(src, url, prm)
        as_of_fn = (lambda s: started.timestamp()) if mode == "early" else (lambda s: s - M.HORIZON_MIN * 60)
        rows, ev_meta = compute(comp, elo_p, run_root, reqs, as_of_fn)
        last_ev = max(_ts(m["retrieved_at"]) for m in ev_meta)
        finalized = now_fn()
        for r in rows:
            if "skip" in r:
                continue
            start = _ts(r["start"])
            cutoff = start - M.HORIZON_MIN * 60
            rec = {"schema": SCHEMA, "mode": mode, "run_id": run_id, "competition": key, **r,
                   "scheduled_cutoff": _iso(cutoff), "evidence_cutoff": _iso(cutoff),
                   "capture_started_at": iso(started), "evidence_last_retrieved_at": _iso(last_ev),
                   "finalized_at": iso(finalized), "model": {"name": "elo", "params": elo_p},
                   "inputs": {"phase1_protocol_sha256": sha256_file(EV.protocol_path(C.PHASE1_PROTOCOL)),
                              "normalized_events_sha256": sha256_file(NORMALIZED / key / "events.jsonl"),
                              "evidence_root": run_root.relative_to(C.CAPTURE_CACHE.parents[3]).as_posix(),
                              "evidence": ev_meta, "requests": [[s, u, p] for s, u, p in reqs]}}
            t0 = started.timestamp()
            if mode == "early":
                if cutoff <= t0:
                    continue
                rec["status"] = "EARLY_SNAPSHOT"
                rec["horizon_minutes"] = round((start - t0) / 60, 1)
                rec["note"] = "development snapshot before the T-60 cutoff; not a checkpoint forecast"
            else:
                if t0 < cutoff - win:
                    continue
                if start <= t0:
                    continue
                if t0 > cutoff:
                    rec.update({"status": "MISSED", "reason": "capture started after the scheduled cutoff",
                                "p_home": None})
                elif last_ev > cutoff or _ts(iso(finalized)) > cutoff:
                    rec.update({"status": "MISSED", "reason": "evidence retrieved or record finalized after the "
                                                              "evidence cutoff", "p_home": None})
                else:
                    rec["status"] = "CHECKPOINT"
                    rec["horizon_minutes"] = round((start - _ts(rec["as_of"])) / 60, 1)
            rec["record_sha256"] = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
            path = C.CAPTURE_DIR / mode / started.strftime("%Y-%m-%d") / \
                f"{run_id}__{key}__{r['source_game_id'].replace(':', '_')}.json"
            if path.exists():
                raise SystemExit(f"{path} exists; capture records are write-once")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec, sort_keys=True, indent=1) + "\n", encoding="utf-8")
            written += 1
            log(f"capture {rec['status']} {key} {r['kalshi_event_ticker']} p_home={rec['p_home']} "
                f"market_mid={r['market_home']['mid']} start={r['start']}")
        skipped = [r for r in rows if "skip" in r]
        if skipped:
            log(f"capture {key}: {len(skipped)} Kalshi events skipped ({sorted({r['skip'] for r in skipped})})")
    log(f"capture {mode}: {written} records, run {run_id}, evidence {run_root}")
    return 0


def replay(record_path: str, log) -> int:
    rec = json.loads(Path(record_path).read_text(encoding="utf-8"))
    if rec.get("schema") != SCHEMA:
        raise SystemExit("not a Phase 2 capture record")
    comp = {c.key: c for c in all_competitions()[0]}[rec["competition"]]
    if sha256_file(NORMALIZED / comp.key / "events.jsonl") != rec["inputs"]["normalized_events_sha256"]:
        raise SystemExit("frozen history changed since capture")
    run_root = C.CAPTURE_CACHE / rec["run_id"]
    reqs = [(s, u, p) for s, u, p in rec["inputs"]["requests"]]
    as_of = _ts(rec["as_of"])
    try:
        rows, ev_meta = compute(comp, rec["model"]["params"], run_root, reqs, lambda s: as_of)
    except CacheMiss as exc:
        raise SystemExit(f"evidence missing from the run cache: {exc}")
    match = [r for r in rows if r.get("source_game_id") == rec["source_game_id"]]
    if not match:
        log("REPLAY FAILED: event not reconstructed from saved evidence")
        return 1
    r = match[0]
    want = rec["p_home"] if rec["status"] != "MISSED" else None
    got = r["p_home"] if rec["status"] != "MISSED" else None
    ok = got == want and r["market_home"] == rec["market_home"] and \
        [m["sha256"] for m in ev_meta] == [m["sha256"] for m in rec["inputs"]["evidence"]]
    log(f"replay {rec['status']} {rec['competition']} {rec['kalshi_event_ticker']}: recorded p_home={want} "
        f"replayed={got} market equal={r['market_home'] == rec['market_home']} -> {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1
