"""Pins checked before any capture record is written, and again on replay.

* Phase 1 code hash and Phase 2 code hash (unchanged frozen code),
* exact bytes of ``sports_phase1_v2.json`` and ``sports_phase2_v1.json`` (their artifact manifests),
* the live code hash frozen in ``sports_phase2_capture_v2.json``,
* every local file a prediction or link reads: normalized events/contracts, participant crosswalk
  and the series inventory (sha256 frozen in the capture protocol).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .. import evaluate as EV
from ..competitions import INVENTORY_CSV
from ..core import CROSSWALK, NORMALIZED, PROTOCOLS, REPO_ROOT, sha256_bytes, sha256_file, utc_now_iso, write_json
from ..phase2 import config as P2
from ..phase2 import pipeline as P2P
from . import config as C

LIVE_CODE_FILES = ["config.py", "discover.py", "sources.py", "predict.py", "pins.py", "capture.py", "replay.py"]


def live_code_hash() -> str:
    here = Path(__file__).parent
    return sha256_bytes(b"".join(sha256_file(here / f).encode() for f in LIVE_CODE_FILES))


def protocol_path() -> Path:
    return PROTOCOLS / f"{C.PROTOCOL}.json"


def rel(p: Path) -> str:
    return p.resolve().relative_to(REPO_ROOT).as_posix()


def dependency_paths(comp) -> list[Path]:
    out = [NORMALIZED / comp.key / "events.jsonl", NORMALIZED / comp.key / "contracts.jsonl", INVENTORY_CSV]
    cw = CROSSWALK / f"{comp.key}_participants.json"
    if comp.source != "kalshi_only" and cw.exists():
        out.append(cw)
    return out


@lru_cache(maxsize=None)
def _sha(path: str) -> str | None:
    p = REPO_ROOT / path
    return sha256_file(p) if p.exists() else None


def dependency_hashes(comp, fresh: bool = False) -> dict:
    if fresh:
        return {rel(p): (sha256_file(p) if p.exists() else None) for p in dependency_paths(comp)}
    return {rel(p): _sha(rel(p)) for p in dependency_paths(comp)}


def _manifest_sha(protocol: str) -> str | None:
    m = json.loads((PROTOCOLS / f"{protocol}.artifacts.json").read_text(encoding="utf-8"))
    return m["files"].get(f"research/sports/protocols/{protocol}.json")


def global_pins() -> dict:
    return {"phase1_code_hash": EV.code_hash(), "phase2_code_hash": P2P.code_hash2(),
            "live_code_hash": live_code_hash(),
            "phase1_protocol_sha256": sha256_file(PROTOCOLS / f"{C.PHASE1_PROTOCOL}.json"),
            "phase2_protocol_sha256": sha256_file(PROTOCOLS / f"{C.PARENT_PROTOCOL}.json")}


def load_protocol() -> dict:
    p = protocol_path()
    if not p.exists():
        raise SystemExit(f"{p.name} not frozen; run `python -m research.sports.live.run freeze` first")
    return json.loads(p.read_text(encoding="utf-8"))


def check(proto: dict, comps, fresh: bool = False) -> list[str]:
    """Problems (empty = every pin holds) for the given competitions."""
    problems = []
    g = global_pins()
    if g["phase1_code_hash"] != P2.PHASE1_CODE_HASH:
        problems.append("Phase 1 code hash changed")
    proto2 = json.loads((PROTOCOLS / f"{C.PARENT_PROTOCOL}.json").read_text(encoding="utf-8"))
    if g["phase2_code_hash"] != proto2["code_hash_phase2"]:
        problems.append("Phase 2 code hash changed")
    for name, protocol in (("phase1_protocol_sha256", C.PHASE1_PROTOCOL), ("phase2_protocol_sha256", C.PARENT_PROTOCOL)):
        if g[name] != _manifest_sha(protocol):
            problems.append(f"{protocol}.json differs from its artifact manifest")
    for k, v in g.items():
        if proto["pins"].get(k) != v:
            problems.append(f"{k} differs from {C.PROTOCOL}")
    for c in comps:
        want = proto["dependencies"].get(c.key)
        if want is None:
            problems.append(f"{c.key}: no frozen dependency pins")
            continue
        have = dependency_hashes(c, fresh)
        for path, sha in sorted(want.items()):
            if have.get(path) != sha:
                problems.append(f"{c.key}: dependency changed: {path}")
        for path in sorted(set(have) - set(want)):
            problems.append(f"{c.key}: unpinned dependency: {path}")
    return problems


def freeze(comps, log) -> dict:
    """Write the capture protocol once; an existing protocol must match the recomputed body exactly."""
    from . import capture as CP
    proto1 = EV.load_protocol(C.PHASE1_PROTOCOL)
    g = global_pins()
    if g["phase1_code_hash"] != P2.PHASE1_CODE_HASH:
        raise SystemExit("Phase 1 code hash changed; refusing to freeze")
    for protocol, key in ((C.PHASE1_PROTOCOL, "phase1_protocol_sha256"), (C.PARENT_PROTOCOL, "phase2_protocol_sha256")):
        if g[key] != _manifest_sha(protocol):
            raise SystemExit(f"{protocol}.json differs from its artifact manifest; refusing to freeze")
    deps, data_ok = {}, {}
    for c in comps:
        deps[c.key] = dependency_hashes(c, fresh=True)
        e = proto1["competitions"].get(c.key) or {}
        d = e.get("data") or {}
        data_ok[c.key] = (deps[c.key].get(rel(NORMALIZED / c.key / "contracts.jsonl")) == d.get("contracts")
                          and deps[c.key].get(rel(NORMALIZED / c.key / "events.jsonl")) == d.get("events"))
    bad = sorted(k for k, ok in data_ok.items() if not ok)
    if bad:
        raise SystemExit(f"normalized data differ from {C.PHASE1_PROTOCOL} for {bad[:5]}; refusing to freeze")
    body = {"protocol": C.PROTOCOL, "parent_protocol": C.PARENT_PROTOCOL, "phase1_protocol": C.PHASE1_PROTOCOL,
            "research_only": True, "no_bet": True, "scheduler": "none (manual invocation only)",
            "parent_preserved": f"{C.PARENT_PROTOCOL} protocol, journals and results are unchanged; this protocol "
                                "only adds a prospective collector",
            "pins": g, "live_code_files": LIVE_CODE_FILES,
            "rules": {"modes": C.MODES, "statuses": C.STATUSES, "recording_rules": C.RECORDING_RULES,
                      "horizon_minutes": C.HORIZON_MIN, "window_minutes": C.WINDOW_MIN,
                      "candidates": {"order": ["frozen", "calibrated", "market", "blend"], **P2.CANDIDATES},
                      "primary_model": "phase2.folds.primary_row over the Phase 1 predictor output (no Elo substitution)",
                      "as_of_view": "events whose result is not released (frozen Phase 1 release rule) by "
                                    "prediction_as_of lose every outcome-derived field; pregame fields are kept",
                      "quote": C.QUOTE, "market_families": C.MARKET_FAMILIES, "contract_rules": C.CONTRACT_RULES,
                      "discovery": {"milestone_min_start": C.MILESTONE_MIN_START, "page_limits": C.PAGE_LIMITS,
                                    "max_pages": C.MAX_PAGES, "window_discovery_hours": C.WINDOW_DISCOVERY_H,
                                    "early_lookahead_hours": C.EARLY_LOOKAHEAD_H,
                                    "history_overlap_days": C.HISTORY_OVERLAP_DAYS},
                      "network": C.NETWORK, "idempotency": CP.IDEMPOTENCY},
            "adapters": C.ADAPTERS, "comparability": C.COMPARABILITY,
            "dependencies": deps}
    path = protocol_path()
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "frozen_at"} != json.loads(json.dumps(body)):
            raise SystemExit(f"{path.name} is frozen and the recomputed content differs; bump the protocol version")
        log(f"{path.name} unchanged")
        return old
    body["frozen_at"] = utc_now_iso()
    write_json(path, body)
    log(f"capture protocol frozen: {path} ({len(deps)} competitions pinned)")
    return body
