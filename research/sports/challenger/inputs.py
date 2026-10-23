"""Verify frozen replay inputs. Mismatch or a missing essential file stops replay."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from .. import evaluate as EV
from ..competitions import INVENTORY_CSV, all_competitions
from ..core import NORMALIZED, PROTOCOLS, REPO_ROOT, request_key, sha256_bytes, sha256_file, sha256_json
from ..live import config as LC
from ..live import pins as PN
from ..phase2 import config as P2
from ..phase2 import pipeline as P2P
from .constants import (
    CAPTURE_PROTOCOL, CAPTURE_PROTOCOL_SHA256, COMPETITIONS, FROZEN_CONTRACTS_SHA256,
    FROZEN_EVENTS_SHA256, INVENTORY_SHA256, LISTING_GZ, LISTING_PAYLOAD_SHA256,
    PHASE1_PROTOCOL, PHASE1_PROTOCOL_SHA256, PHASE2_PROTOCOL, PHASE2_PROTOCOL_SHA256,
    SNAP,
)


class InputError(RuntimeError):
    """Essential frozen input missing or mismatched."""


def _sha(path: Path) -> str:
    if not path.is_file():
        raise InputError(f"missing essential file: {path.relative_to(REPO_ROOT).as_posix()}")
    return sha256_file(path)


def load_listing() -> dict[str, tuple[str, int]]:
    raw = gzip.decompress(LISTING_GZ.read_bytes())
    got = sha256_bytes(raw)
    if got != LISTING_PAYLOAD_SHA256:
        raise InputError(f"snapshot listing payload sha256 {got} != {LISTING_PAYLOAD_SHA256}")
    out = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        path, digest, size_s = line.decode("utf-8").split("\t")
        out[path.replace("\\", "/")] = (digest, int(size_s))
    return out


def snap_for(live_rel: str) -> Path | None:
    rel = live_rel.replace("\\", "/")
    if rel.startswith("data/sports/phase2/capture_v2_scores/"):
        return SNAP / "sports_scores" / rel[len("data/sports/phase2/capture_v2_scores/"):]
    if rel.startswith("data/sports/phase2/capture_v2/"):
        return SNAP / "sports_capture_v2" / rel[len("data/sports/phase2/capture_v2/"):]
    return None


def read_verified_snapshot(live_rel: str, listing: dict[str, tuple[str, int]]) -> tuple[bytes, dict]:
    rel = live_rel.replace("\\", "/")
    if rel not in listing:
        raise InputError(f"not in frozen listing: {rel}")
    path = snap_for(rel)
    if path is None or not path.is_file():
        raise InputError(f"snapshot file missing for {rel}")
    data = path.read_bytes()
    want, size = listing[rel]
    if len(data) != size or sha256_bytes(data) != want:
        raise InputError(f"snapshot bytes differ from listing for {rel}")
    try:
        obj = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise InputError(f"unreadable JSON {rel}: {exc}") from exc
    return data, obj


def verify_static_inputs() -> dict:
    """Check listing, protocols, code hashes and frozen ITF normalized history.

    Local ``data/sports/normalized`` files are used only after they match the capture
    protocol pins. Today's copy is never substituted when the hash differs.
    """
    listing = load_listing()
    checks = {
        "listing_payload_sha256": LISTING_PAYLOAD_SHA256,
        "capture_protocol_sha256": _sha(PROTOCOLS / f"{CAPTURE_PROTOCOL}.json"),
        "phase1_protocol_sha256": _sha(PROTOCOLS / f"{PHASE1_PROTOCOL}.json"),
        "phase2_protocol_sha256": _sha(PROTOCOLS / f"{PHASE2_PROTOCOL}.json"),
        "inventory_sha256": _sha(INVENTORY_CSV),
        "phase1_code_hash": EV.code_hash(),
        "phase2_code_hash": P2P.code_hash2(),
        "live_code_hash": PN.live_code_hash(),
    }
    want = {
        "capture_protocol_sha256": CAPTURE_PROTOCOL_SHA256,
        "phase1_protocol_sha256": PHASE1_PROTOCOL_SHA256,
        "phase2_protocol_sha256": PHASE2_PROTOCOL_SHA256,
        "inventory_sha256": INVENTORY_SHA256,
        "phase1_code_hash": P2.PHASE1_CODE_HASH,
        "phase2_code_hash": json.loads((PROTOCOLS / f"{PHASE2_PROTOCOL}.json").read_text(encoding="utf-8"))[
            "code_hash_phase2"],
        "live_code_hash": json.loads((PROTOCOLS / f"{CAPTURE_PROTOCOL}.json").read_text(encoding="utf-8"))["pins"][
            "live_code_hash"],
    }
    problems = [f"{k}: {checks[k]} != {want[k]}" for k in want if checks[k] != want[k]]
    if PN.global_pins()["phase1_protocol_sha256"] != PHASE1_PROTOCOL_SHA256:
        problems.append("phase1 protocol pin mismatch")
    events, contracts = {}, {}
    for key in COMPETITIONS:
        ev = NORMALIZED / key / "events.jsonl"
        ct = NORMALIZED / key / "contracts.jsonl"
        ev_sha, ct_sha = _sha(ev), _sha(ct)
        if ev_sha != FROZEN_EVENTS_SHA256[key]:
            problems.append(f"{key} events.jsonl sha256 {ev_sha} != frozen {FROZEN_EVENTS_SHA256[key]}")
        if ct_sha != FROZEN_CONTRACTS_SHA256[key]:
            problems.append(f"{key} contracts.jsonl sha256 {ct_sha} != frozen {FROZEN_CONTRACTS_SHA256[key]}")
        events[key] = ev_sha
        contracts[key] = ct_sha
    proto_deps = json.loads((PROTOCOLS / f"{CAPTURE_PROTOCOL}.json").read_text(encoding="utf-8"))["dependencies"]
    for key in COMPETITIONS:
        pinned = proto_deps[key]
        if pinned[f"data/sports/normalized/{key}/events.jsonl"] != FROZEN_EVENTS_SHA256[key]:
            problems.append(f"{key}: capture protocol events pin disagrees with study constant")
        if pinned[f"data/sports/normalized/{key}/contracts.jsonl"] != FROZEN_CONTRACTS_SHA256[key]:
            problems.append(f"{key}: capture protocol contracts pin disagrees with study constant")
    if problems:
        raise InputError("frozen input verification failed: " + "; ".join(problems))
    if not SNAP.is_dir():
        raise InputError(f"snapshot directory missing: {SNAP}")
    comps, _, _ = all_competitions()
    by_key = {c.key: c for c in comps}
    missing = [k for k in COMPETITIONS if k not in by_key]
    if missing:
        raise InputError(f"competitions missing from registry: {missing}")
    return {
        "ok": True,
        "listing_rows": len(listing),
        "checks": checks,
        "events_sha256": events,
        "contracts_sha256": contracts,
        "competitions": {k: by_key[k] for k in COMPETITIONS},
        "listing": listing,
    }


def verify_capture_record(rec: dict) -> None:
    if rec.get("schema") != LC.SCHEMA:
        raise InputError("not a sports_phase2_capture_v2 record")
    want = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    if rec.get("record_sha256") != want:
        raise InputError("capture record checksum mismatch")
    pins = rec.get("pins") or {}
    now = PN.global_pins()
    diff = sorted(k for k, v in pins.items() if now.get(k) != v)
    if diff:
        raise InputError(f"capture pins differ from current frozen hashes: {diff}")
    if rec.get("capture_protocol_sha256") != CAPTURE_PROTOCOL_SHA256:
        raise InputError("capture_protocol_sha256 differs from frozen capture protocol")


def evidence_bodies_ok(rec: dict) -> tuple[bool, str | None, int, int]:
    """Return (ok, reason, present, missing). Bytes must match the record; no network."""
    entries = rec.get("evidence") or []
    if not entries:
        return False, "no evidence list on capture record", 0, 0
    root = LC.EVIDENCE_ROOT / rec["run_id"]
    present = missing = 0
    for e in entries:
        if request_key(e["url"], e["params"]) != e["key"]:
            return False, f"evidence key mismatch for {e['url']}", present, missing + 1
        body_p = root / e["source"] / e["key"][:2] / f"{e['key']}.body"
        if not body_p.is_file():
            missing += 1
            continue
        if sha256_bytes(body_p.read_bytes()) != e["sha256"]:
            return False, f"evidence bytes changed: {e['key'][:16]}", present, missing + 1
        present += 1
    if missing:
        return False, f"{missing} evidence bodies missing under {root.as_posix()}", present, missing
    late = [e for e in entries if e.get("received_at") and e["received_at"] > rec["prediction_as_of"]]
    if late:
        return False, f"{len(late)} evidence retrievals recorded after prediction_as_of", present, missing
    return True, None, present, missing
