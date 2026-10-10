"""Write-once settlement scores for accepted WINDOW_CAPTURE records.

Only ``accepted/checkpoint`` records with status WINDOW_CAPTURE are scored. Before the first score of a
record, its checksum, pins, prediction dependencies and raw prediction evidence are verified (the same
checks as replay), together with the timing invariant and every candidate probability. A finalized score
copies each candidate verbatim from the record, pins the record's path and ``record_sha256`` and is
created exclusively; it is never rewritten. Non-final settlement states are run diagnostics only.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..core import sha256_json
from ..live import capture as CP
from ..live import config as LC
from ..live import discover as D
from ..live import predict as LP
from ..live import replay as RP
from . import config as C
from . import settle as S


class SourceRefused(RuntimeError):
    pass


class ScoreRefused(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_us(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _bytes(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1) + "\n").encode("utf-8")


def valid_p(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def event_key(rec: dict) -> str:
    return f"{rec['competition']}|{rec['cluster']}"


def rel(p: Path) -> str:
    return CP._relpath(Path(p))


def score_path(comp: str, cluster: str, ticker: str) -> Path:
    return C.SCORE_DIR / "finalized" / comp / CP.safe(cluster) / f"{ticker}.json"


# ---------------------------------------------------------------- record scan and verification

def scan() -> tuple[list[tuple[Path, dict]], Counter]:
    """(eligible accepted WINDOW_CAPTURE records, exclusion counts by record status/location)."""
    root = LC.CAPTURE_DIR
    eligible, excluded = [], Counter()
    for p in sorted((root / "accepted").glob("*/*/*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        slot = p.parent.parent.name
        if slot == "checkpoint" and rec.get("status") == C.SCORED_STATUS:
            eligible.append((p, rec))
        else:
            excluded[f"accepted/{slot}:{rec.get('status')}"] += 1
    for p in sorted((root / "attempts").glob("*/*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        excluded[f"attempt:{rec.get('status')}"] += 1
    return eligible, excluded


def check_probabilities(rec: dict) -> None:
    for c in rec["contracts"]:
        for k in LP.CANDIDATES:
            cand = c["candidates"].get(k)
            if cand is None:
                raise SourceRefused(f"{c['ticker']}: candidate {k} missing")
            if "p" in cand and not valid_p(cand["p"]):
                raise SourceRefused(f"{c['ticker']}: invalid {k} probability {cand['p']!r}")
            if "p" not in cand and not cand.get("unavailable"):
                raise SourceRefused(f"{c['ticker']}: candidate {k} has neither p nor an unavailability reason")


def verify_source(path: Path, rec: dict) -> None:
    """Full pre-scoring verification of an accepted record (raises SourceRefused / ReplayRefused / EvidenceError)."""
    want = (LC.CAPTURE_DIR / "accepted" / "checkpoint").resolve()
    if Path(path).resolve().parent.parent != want:
        raise SourceRefused("not an accepted checkpoint record")
    RP.check_record(rec)
    if rec.get("status") != C.SCORED_STATUS or rec.get("mode") != "window":
        raise SourceRefused(f"status {rec.get('status')} / mode {rec.get('mode')} is not a window capture")
    by_key, _, _ = CP._comp_maps()
    comp = by_key.get(rec["competition"])
    if comp is None:
        raise SourceRefused(f"unknown competition {rec['competition']}")
    RP.check_pins(rec, comp)
    RP.check_evidence(rec, LC.EVIDENCE_ROOT / rec["run_id"])
    t_open, t_cut = CP._ts(rec["window_open"]), CP._ts(rec["scheduled_cutoff"])
    t_as, t_fin = rec["prediction_as_of_ts"], CP._ts(rec["finalized_at"])
    if not (t_open <= t_as <= t_fin <= t_cut):
        raise SourceRefused("timing: requires window_open <= prediction_as_of <= finalized_at <= cutoff")
    check_probabilities(rec)


def verify_existing(path: Path, src_rel: str, src_sha: str) -> dict:
    sc = json.loads(path.read_text(encoding="utf-8"))
    if sc.get("schema") != C.SCHEMA:
        raise ScoreRefused(f"{path}: not a {C.SCHEMA} file")
    if sc.get("score_sha256") != sha256_json({k: v for k, v in sc.items() if k != "score_sha256"}):
        raise ScoreRefused(f"{path}: score checksum mismatch")
    if sc.get("source_record") != src_rel or sc.get("source_record_sha256") != src_sha:
        raise ScoreRefused(f"{path}: source record identity differs from the score's pinned source")
    return sc


def check_settlement_evidence(sc: dict) -> None:
    RP.check_evidence({"evidence": sc["settlement_evidence"]}, C.EVIDENCE_ROOT / sc["settlement_run_id"])


# ---------------------------------------------------------------- scoring run

def build_score(path: Path, rec: dict, c: dict, st: dict, run_id: str, refs: list[dict], now: datetime) -> dict:
    sc = {
        "schema": C.SCHEMA, "protocol": C.PROTOCOL, "source_protocol": C.SOURCE_PROTOCOL,
        "research_only": True, "no_bet": True,
        "source_record": rel(path), "source_record_sha256": rec["record_sha256"], "source_run_id": rec["run_id"],
        "competition": rec["competition"], "sport": rec.get("sport"), "cluster": rec["cluster"],
        "event_key": event_key(rec), "ticker": c["ticker"], "series": c["series"], "family": c["family"],
        "cohort": c.get("cohort"), "side": c.get("side"), "strike": c.get("strike"),
        "horizon_minutes": rec.get("horizon_minutes"), "scheduled_cutoff": rec["scheduled_cutoff"],
        "prediction_as_of": rec["prediction_as_of"],
        "candidates": c["candidates"],
        "settlement": {"state": st["state"], "detail": st["detail"], "market": st["market"]},
        "y": C.BINARY.get(st["state"]),
        "settlement_run_id": run_id, "settlement_evidence": refs, "scored_at": _iso_us(now),
    }
    sc = json.loads(json.dumps(sc))
    sc["score_sha256"] = sha256_json(sc)
    return sc


def write_score(p: Path, sc: dict) -> bool:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "xb") as f:
            f.write(_bytes(sc))
        return True
    except FileExistsError:
        return False


def run(log, now_fn=None, session=None) -> int:
    now_fn = now_fn or _now
    run_id = now_fn().strftime("%Y%m%dT%H%M%S%fZ")
    ev = D.Evidence(C.EVIDENCE_ROOT / run_id, now_fn, session)
    eligible, excluded = scan()
    counts, diags = Counter(), []

    def diag(rec, ticker, state, detail):
        counts[state] += 1
        diags.append({"competition": rec["competition"], "cluster": rec["cluster"], "ticker": ticker,
                      "state": state, "detail": detail})

    for path, rec in eligible:
        ok = [c for c in rec["contracts"] if c["status"] == "ok"]
        excluded["contract_not_ok"] += len(rec["contracts"]) - len(ok)
        src_rel = rel(path)
        todo = []
        for c in ok:
            sp = score_path(rec["competition"], rec["cluster"], c["ticker"])
            if not sp.exists():
                todo.append(c)
                continue
            try:
                RP.check_record(rec)
                verify_existing(sp, src_rel, rec["record_sha256"])
                counts["already_finalized"] += 1
            except (RP.ReplayRefused, ScoreRefused) as exc:
                diag(rec, c["ticker"], "SCORE_REFUSED", str(exc))
        if not todo:
            continue
        try:
            verify_source(path, rec)
        except (SourceRefused, RP.ReplayRefused, D.EvidenceError) as exc:
            for c in todo:
                diag(rec, c["ticker"], "SOURCE_REFUSED", str(exc))
            continue
        res, keys = S.lookup(ev.get, rec["kalshi_event_tickers"], [c["ticker"] for c in todo])
        refs = ev.refs([k for k in keys if k in ev.entries])
        for c in todo:
            st = res[c["ticker"]]
            if st["state"] not in C.FINAL_STATES:
                diag(rec, c["ticker"], st["state"], st["detail"])
                continue
            sc = build_score(path, rec, c, st, run_id, refs, now_fn())
            try:
                check_settlement_evidence(sc)
            except (RP.ReplayRefused, D.EvidenceError) as exc:
                diag(rec, c["ticker"], "LOOKUP_FAILED", f"settlement evidence check: {exc}")
                continue
            if write_score(score_path(rec["competition"], rec["cluster"], c["ticker"]), sc):
                counts[f"finalized:{st['state']}"] += 1
            else:
                counts["already_finalized"] += 1
    summary = {"protocol": C.PROTOCOL, "run_id": run_id, "finished_at": _iso_us(now_fn()),
               "eligible_records": len(eligible), "counts": dict(counts), "excluded": dict(excluded),
               "diagnostics": diags}
    out = C.SCORE_DIR / "runs" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_bytes(summary))
    log(f"settle run {run_id}: {len(eligible)} eligible WINDOW_CAPTURE records; {dict(sorted(counts.items()))}")
    log(f"excluded from scoring: {dict(sorted(excluded.items()))}")
    log(f"summary {out}")
    return 0


def verify_scores(log) -> int:
    """Re-check every finalized score: checksum, source record identity and settlement evidence bytes."""
    bad = 0
    paths = sorted((C.SCORE_DIR / "finalized").glob("*/*/*.json"))
    from ..core import REPO_ROOT
    for p in paths:
        try:
            sc = json.loads(p.read_text(encoding="utf-8"))
            src = REPO_ROOT / sc["source_record"]
            if not src.exists():
                raise ScoreRefused(f"source record missing: {sc['source_record']}")
            rec = json.loads(src.read_text(encoding="utf-8"))
            RP.check_record(rec)
            verify_existing(p, rel(src), rec["record_sha256"])
            check_settlement_evidence(sc)
        except (ScoreRefused, RP.ReplayRefused, D.EvidenceError, KeyError) as exc:
            bad += 1
            log(f"SCORE REFUSED {p}: {exc}")
    log(f"verified {len(paths)} finalized scores: {len(paths) - bad} OK, {bad} refused")
    return 0 if not bad else 1
