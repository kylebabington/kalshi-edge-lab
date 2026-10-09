"""Per-protocol artifact manifests and verification.

Each protocol has its own tracked manifest ``research/sports/protocols/<protocol>.artifacts.json``
listing the sha256 of its protocol file, prediction journals, result files and tracked docs.
``verify`` refuses missing artifacts, unexpected extra journals and hash mismatches.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import PREDICTIONS, PROTOCOLS, REPO_ROOT, RESULTS, sha256_file, sha256_json, utc_now_iso, write_json

DOCS = REPO_ROOT / "docs" / "research"
RESULT_FILES = ["scored.jsonl", "metrics.json", "evaluation_status.json", "benchmark_metrics.json",
                "benchmark_quotes.jsonl", "closing_lines.json", "report_hashes.json"]
DOC_FILES = {
    "sports_phase1_v1": ["sports_phase1_results_v1.md", "sports_phase1_coverage_v1.csv",
                         "sports_phase1_results_v1.csv", "sports_audit_supplement_v1_1.md"],
    "sports_phase1_v2": ["sports_phase1_results_v2.md", "sports_phase1_coverage_v2.csv",
                         "sports_phase1_results_v2.csv", "sports_phase1_v1_to_v2_comparison.md"],
}
SUMMARY_KEYS = ["protocol_file_sha256", "prediction_journals", "prediction_journals_manifest_sha256",
                "scored_rows_sha256", "metrics_sha256"]


def manifest_path(protocol: str) -> Path:
    return PROTOCOLS / f"{protocol}.artifacts.json"


def _rel(p: Path) -> str:
    return p.resolve().relative_to(REPO_ROOT).as_posix()


def journal_paths(protocol: str) -> list[Path]:
    d = PREDICTIONS / protocol
    return sorted(d.glob("*.jsonl")) if d.exists() else []


def expected_paths(protocol: str) -> list[Path]:
    if protocol not in DOC_FILES:
        raise SystemExit(f"no artifact list for protocol {protocol}")
    return ([PROTOCOLS / f"{protocol}.json"] + journal_paths(protocol) +
            [RESULTS / protocol / f for f in RESULT_FILES] + [DOCS / f for f in DOC_FILES[protocol]])


def summary_hashes(protocol: str) -> dict:
    """The run-level hashes recorded by the report stage (report_hashes.json)."""
    js = journal_paths(protocol)
    out_dir = RESULTS / protocol
    return {"protocol_file_sha256": sha256_file(PROTOCOLS / f"{protocol}.json"),
            "prediction_journals": len(js),
            "prediction_journals_manifest_sha256": sha256_json({p.name: sha256_file(p) for p in js}),
            "scored_rows_sha256": sha256_file(out_dir / "scored.jsonl"),
            "metrics_sha256": sha256_file(out_dir / "metrics.json")}


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


def verify(protocol: str) -> list[str]:
    """Problems found (empty list = verified)."""
    path = manifest_path(protocol)
    if not path.exists():
        return [f"manifest missing: {_rel(path)}"]
    man = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for rel, digest in sorted(man["files"].items()):
        p = REPO_ROOT / rel
        if not p.exists():
            problems.append(f"missing: {rel}")
        elif sha256_file(p) != digest:
            problems.append(f"hash mismatch: {rel}")
    listed = {rel for rel in man["files"]}
    for p in journal_paths(protocol):
        if _rel(p) not in listed:
            problems.append(f"unexpected journal: {_rel(p)}")
    return problems
