"""Offline reproduction of a capture record (no network).

Order of checks (any failure refuses reproduction):
1. record checksum (``record_sha256`` over the record without that field),
2. pins: Phase 1 / Phase 2 / live code hashes and protocol bytes equal those recorded,
3. dependencies: every local file the prediction read (normalized history, crosswalk, inventory)
   hashes to the recorded sha256,
4. evidence: every recorded response body re-hashes to its recorded sha256,
5. recomputation with the same code path reproduces the contracts, picks and as-of statistics exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import evaluate as EV
from ..core import PROTOCOLS, request_key, sha256_bytes, sha256_file, sha256_json
from . import capture as CP
from . import config as C
from . import discover as D
from . import pins as PN
from . import predict as P


class ReplayRefused(RuntimeError):
    pass


def check_record(rec: dict) -> None:
    if rec.get("schema") != C.SCHEMA:
        raise ReplayRefused("not a sports_phase2_capture_v2 record")
    want = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    if rec.get("record_sha256") != want:
        raise ReplayRefused("record checksum mismatch (record edited after capture)")


def check_pins(rec: dict, comp) -> None:
    now = PN.global_pins()
    diff = sorted(k for k, v in rec["pins"].items() if now.get(k) != v)
    if diff:
        raise ReplayRefused(f"pins changed since capture: {diff}")
    if sha256_file(PN.protocol_path()) != rec["capture_protocol_sha256"]:
        raise ReplayRefused(f"{C.PROTOCOL}.json changed since capture")
    have = PN.dependency_hashes(comp, fresh=True)
    diff = sorted(p for p, s in rec["dependencies"].items() if have.get(p) != s)
    if diff or set(have) != set(rec["dependencies"]):
        raise ReplayRefused(f"prediction dependencies changed since capture: {diff or sorted(set(have) ^ set(rec['dependencies']))}")


def check_evidence(rec: dict, root: Path) -> None:
    for e in rec["evidence"]:
        if request_key(e["url"], e["params"]) != e["key"]:
            raise ReplayRefused(f"evidence key mismatch for {e['url']}")
        p = root / e["source"] / e["key"][:2] / f"{e['key']}.body"
        if not p.exists():
            raise ReplayRefused(f"evidence bytes missing: {p}")
        if sha256_bytes(p.read_bytes()) != e["sha256"]:
            raise ReplayRefused(f"evidence bytes changed since capture: {p}")


def replay_record(rec: dict) -> dict:
    check_record(rec)
    if rec["status"] not in ("WINDOW_CAPTURE", "EARLY_SNAPSHOT"):
        return {"status": rec["status"], "ok": True, "note": "no probabilities to reproduce; checksum verified"}
    by_key, series_spec, _ = CP._comp_maps()
    comp = by_key[rec["competition"]]
    check_pins(rec, comp)
    root = C.EVIDENCE_ROOT / rec["run_id"]
    check_evidence(rec, root)
    saved = D.SavedEvidence(root, rec["evidence"])
    proto1 = EV.load_protocol(C.PHASE1_PROTOCOL)
    groups = json.loads((PROTOCOLS / f"{C.PARENT_PROTOCOL}.json").read_text(encoding="utf-8"))["groups"]
    ctx = P.build_context(comp, saved.get, rec["context"]["event_tickers"], rec["context"]["history_end"],
                          series_spec[comp.key])
    candles = {}
    for r in rec["candle_requests"]:
        url, params = D.candle_request(r["series"], r["ticker"], r["request_ts"])
        candles[r["ticker"]] = (saved.get("kalshi", url, params), r["request_ts"])
    res = CP.compute_event(comp, proto1["competitions"][comp.key], groups, ctx, rec["cluster"],
                           rec["market_pick_ts"], rec["prediction_as_of_ts"], candles)
    res = json.loads(json.dumps(res))
    mism = [k for k in ("contracts", "market_picks", "as_of_view") if res[k] != rec[k]]
    n = sum(1 for c in rec["contracts"] for k in P.CANDIDATES if "p" in c["candidates"][k])
    return {"status": rec["status"], "ok": not mism, "mismatch": mism, "probabilities": n}


def replay_path(path: Path, log) -> bool:
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        out = replay_record(rec)
    except (ReplayRefused, D.EvidenceError) as exc:
        log(f"REPLAY REFUSED {path}: {exc}")
        return False
    log(f"replay {out['status']} {rec.get('competition')} {rec.get('cluster')}: "
        f"{'OK' if out['ok'] else 'MISMATCH ' + str(out.get('mismatch'))}"
        + (f" ({out['probabilities']} candidate probabilities reproduced)" if 'probabilities' in out else ""))
    return out["ok"]


def replay_all(log) -> int:
    root = C.CAPTURE_DIR
    paths = sorted((root / "accepted").glob("*/*/*.json")) + sorted((root / "attempts").glob("*/*.json"))
    ok = sum(replay_path(p, log) for p in paths)
    log(f"replayed {len(paths)} records: {ok} OK, {len(paths) - ok} refused or mismatched")
    return 0 if ok == len(paths) else 1
