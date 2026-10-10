"""Per-protocol artifact manifests and verification.

Each protocol has its own tracked manifest ``research/sports/protocols/<protocol>.artifacts.json``
listing the sha256 of its protocol file, prediction journals, result files and tracked docs.

Verification has two parts:

* research artifacts (protocol, journals, scored rows, numerical results): exact-byte sha256
  against the original manifest; never normalized;
* tracked human-readable docs: when a versioned supplement
  ``<protocol>.docs_portable_v1.json`` exists, a portable hash (CRLF -> LF, nothing else) so LF
  and CRLF checkouts both verify. The original manifests are never rewritten; their exact doc
  hashes remain informational (they record the Windows working-copy bytes at assembly time).
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import PREDICTIONS, PROTOCOLS, REPO_ROOT, RESULTS, sha256_bytes, sha256_file, sha256_json, utc_now_iso, \
    write_json

DOCS = REPO_ROOT / "docs" / "research"
RESULT_FILES = ["scored.jsonl", "metrics.json", "evaluation_status.json", "benchmark_metrics.json",
                "benchmark_quotes.jsonl", "closing_lines.json", "report_hashes.json"]
DOC_FILES = {
    "sports_phase1_v1": ["sports_phase1_results_v1.md", "sports_phase1_coverage_v1.csv",
                         "sports_phase1_results_v1.csv", "sports_audit_supplement_v1_1.md"],
    "sports_phase1_v2": ["sports_phase1_results_v2.md", "sports_phase1_coverage_v2.csv",
                         "sports_phase1_results_v2.csv", "sports_phase1_v1_to_v2_comparison.md"],
    "sports_phase2_v1": ["sports_phase2_results_v1.md", "sports_phase2_coverage_v1.csv",
                         "sports_phase2_results_v1.csv"],
}
PHASE2_LAYOUT = {
    "results": REPO_ROOT / "data" / "results" / "sports_phase2",
    "journals": REPO_ROOT / "data" / "sports" / "phase2" / "predictions",
    "result_files": ["scored.jsonl", "metrics.json", "evaluation_status.json", "oof_status.json",
                     "market_quotes.jsonl", "report_hashes.json"],
    "oof": REPO_ROOT / "data" / "sports" / "phase2" / "oof",
    "market": REPO_ROOT / "data" / "sports" / "phase2" / "market",
    "market_files": ["sample_manifest.json", "listing_times.json"],
}
PHASE2_PROTOCOLS = {"sports_phase2_v1"}
SUMMARY_KEYS = ["protocol_file_sha256", "prediction_journals", "prediction_journals_manifest_sha256",
                "scored_rows_sha256", "metrics_sha256"]
DOC_RULE = "portable_lf_v1"
DOC_RULE_TEXT = ("portable_lf_v1: read the file bytes, replace every CRLF (\\r\\n) with LF (\\n), change nothing "
                 "else (no trimming, no Unicode normalization, lone CR kept), then sha256")
DOC_SUPPLEMENT_WHY = (
    "Added 2026-10-09 after the original manifest was assembled. The original manifest pinned the exact bytes of the "
    "Windows working copy, where git (core.autocrlf) checks text docs out with CRLF; the repository stores them with "
    "LF, so an LF checkout (Linux, autocrlf=input/false) has different bytes for identical content. Docs are "
    "human-readable summaries, so they get a portable hash; research artifacts keep exact-byte checks. This file was "
    "created only after every listed doc matched its exact hash in the original manifest.")
DOC_SUPPLEMENT_WHY_NEW = (
    "Created together with the original manifest so the tracked documents verify on LF and CRLF checkouts alike "
    "(git core.autocrlf may rewrite their line endings); research artifacts keep exact-byte checks.")


def manifest_path(protocol: str) -> Path:
    return PROTOCOLS / f"{protocol}.artifacts.json"


def docs_supplement_path(protocol: str) -> Path:
    return PROTOCOLS / f"{protocol}.docs_portable_v1.json"


def _rel(p: Path) -> str:
    return p.resolve().relative_to(REPO_ROOT).as_posix()


def _layout(protocol: str) -> tuple[Path, Path, list[str]]:
    if protocol in PHASE2_PROTOCOLS:
        return PHASE2_LAYOUT["results"] / protocol, PHASE2_LAYOUT["journals"] / protocol, PHASE2_LAYOUT["result_files"]
    return RESULTS / protocol, PREDICTIONS / protocol, RESULT_FILES


def journal_paths(protocol: str) -> list[Path]:
    d = _layout(protocol)[1]
    return sorted(d.glob("*.jsonl")) if d.exists() else []


def expected_paths(protocol: str) -> list[Path]:
    if protocol not in DOC_FILES:
        raise SystemExit(f"no artifact list for protocol {protocol}")
    out_dir, _, files = _layout(protocol)
    extra = []
    if protocol in PHASE2_PROTOCOLS:
        oof = PHASE2_LAYOUT["oof"] / protocol
        extra = (sorted(oof.glob("*.jsonl")) if oof.exists() else []) + \
            [PHASE2_LAYOUT["market"] / protocol / f for f in PHASE2_LAYOUT["market_files"]]
    return ([PROTOCOLS / f"{protocol}.json"] + journal_paths(protocol) + extra +
            [out_dir / f for f in files] + [DOCS / f for f in DOC_FILES[protocol]])


def summary_hashes(protocol: str) -> dict:
    """The run-level hashes recorded by the report stage (report_hashes.json)."""
    js = journal_paths(protocol)
    out_dir = _layout(protocol)[0]
    return {"protocol_file_sha256": sha256_file(PROTOCOLS / f"{protocol}.json"),
            "prediction_journals": len(js),
            "prediction_journals_manifest_sha256": sha256_json({p.name: sha256_file(p) for p in js}),
            "scored_rows_sha256": sha256_file(out_dir / "scored.jsonl"),
            "metrics_sha256": sha256_file(out_dir / "metrics.json")}


def portable_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def _is_doc(rel: str) -> bool:
    return rel.startswith("docs/")


def write_manifest(protocol: str, checked_against: Path | None, note: str, log) -> dict:
    missing = [_rel(p) for p in expected_paths(protocol) if not p.exists()]
    if missing:
        raise SystemExit(f"cannot build manifest for {protocol}; missing: {missing[:5]} ({len(missing)} total)")
    files = {_rel(p): sha256_file(p) for p in expected_paths(protocol)}
    check = None
    if checked_against is not None:
        ref = json.loads(Path(checked_against).read_text(encoding="utf-8"))
        now = summary_hashes(protocol)
        diff = {k: (ref.get(k), now[k]) for k in SUMMARY_KEYS if ref.get(k) != now[k]}
        if diff:
            raise SystemExit(f"{protocol} artifacts differ from {checked_against}: {diff}")
        check = {"reference": _rel(Path(checked_against)), "keys": SUMMARY_KEYS, "result": "match",
                 "reference_hashes": {k: ref[k] for k in SUMMARY_KEYS}}
    path = manifest_path(protocol)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old["files"] != files:
            raise SystemExit(f"{path.name} exists and differs from the current artifacts; refusing to overwrite")
        log(f"manifest unchanged {path.name} ({len(files)} files)")
        return old
    body = {"protocol": protocol, "assembled_at": utc_now_iso(), "note": note, "checked_against": check,
            "summary": summary_hashes(protocol), "files": files}
    write_json(path, body)
    log(f"manifest written {path.name} ({len(files)} files)")
    return body


def write_docs_supplement(protocol: str, log, why: str = DOC_SUPPLEMENT_WHY) -> dict:
    """Versioned portable doc hashes, created only from docs that match the original manifest exactly."""
    mpath = manifest_path(protocol)
    if not mpath.exists():
        raise SystemExit(f"original manifest missing for {protocol}")
    man = json.loads(mpath.read_text(encoding="utf-8"))
    files = {}
    for rel, exact in sorted(man["files"].items()):
        if not _is_doc(rel):
            continue
        p = REPO_ROOT / rel
        if not p.exists() or sha256_file(p) != exact:
            raise SystemExit(f"{rel} does not match its exact hash in {mpath.name}; refusing to derive a portable hash")
        files[rel] = {"portable_sha256": portable_sha256(p), "exact_sha256_in_original_manifest": exact}
    body = {"schema": "docs_portable_v1", "protocol": protocol, "rule": DOC_RULE, "rule_text": DOC_RULE_TEXT,
            "why": why, "original_manifest": _rel(mpath),
            "original_manifest_sha256": sha256_file(mpath), "files": files}
    path = docs_supplement_path(protocol)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old["files"] != files or old["original_manifest_sha256"] != body["original_manifest_sha256"]:
            raise SystemExit(f"{path.name} exists and differs; refusing to overwrite")
        log(f"docs supplement unchanged {path.name}")
        return old
    body["created_at"] = utc_now_iso()
    write_json(path, body)
    log(f"docs supplement written {path.name} ({len(files)} docs)")
    return body


def verify_detail(protocol: str) -> dict:
    """{'research': [...], 'docs': [...], 'docs_method': ...}: problems per section (empty = verified)."""
    path = manifest_path(protocol)
    if not path.exists():
        return {"research": [f"manifest missing: {_rel(path)}"], "docs": [], "docs_method": "n/a"}
    man = json.loads(path.read_text(encoding="utf-8"))
    research, docs = [], []
    for rel, digest in sorted(man["files"].items()):
        if _is_doc(rel):
            continue
        p = REPO_ROOT / rel
        if not p.exists():
            research.append(f"missing: {rel}")
        elif sha256_file(p) != digest:
            research.append(f"hash mismatch (exact bytes): {rel}")
    listed = set(man["files"])
    for p in journal_paths(protocol):
        if _rel(p) not in listed:
            research.append(f"unexpected journal: {_rel(p)}")
    sup_path = docs_supplement_path(protocol)
    doc_rels = sorted(r for r in man["files"] if _is_doc(r))
    if sup_path.exists():
        sup = json.loads(sup_path.read_text(encoding="utf-8"))
        method = f"{sup['rule']} ({_rel(sup_path)})"
        if sup["original_manifest_sha256"] != sha256_file(path):
            docs.append(f"{sup_path.name} was derived from a different original manifest")
        if sorted(sup["files"]) != doc_rels:
            docs.append(f"{sup_path.name} does not list the same docs as the original manifest")
        for rel, h in sorted(sup["files"].items()):
            p = REPO_ROOT / rel
            if not p.exists():
                docs.append(f"missing: {rel}")
            elif portable_sha256(p) != h["portable_sha256"]:
                docs.append(f"content mismatch ({sup['rule']}): {rel}")
    else:
        method = "exact bytes (no portable supplement)"
        for rel in doc_rels:
            p = REPO_ROOT / rel
            if not p.exists():
                docs.append(f"missing: {rel}")
            elif sha256_file(p) != man["files"][rel]:
                docs.append(f"hash mismatch (exact bytes): {rel}")
    return {"research": research, "docs": docs, "docs_method": method}


def verify(protocol: str) -> list[str]:
    """All problems found (empty list = verified)."""
    d = verify_detail(protocol)
    return d["research"] + d["docs"]
