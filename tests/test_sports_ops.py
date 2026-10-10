"""Sports operational fixes: filtered runs never overwrite full results; portable doc verification."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.sports import evaluate as EV
from research.sports import manifest as MAN
from research.sports import run as RUN
from research.sports.core import sha256_bytes, sha256_file
from tests.test_sports_phase1_v2 import _setup_eval

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ filtered runs -> subset directory

def _args(**kw):
    base = {"stage": "evaluate", "protocol": "p", "competition": None, "family": None, "source": None, "sport": None}
    return SimpleNamespace(**{**base, **kw})


def test_subset_slug_is_deterministic_and_order_insensitive():
    assert RUN.subset_slug(_args()) is None
    a = RUN.subset_slug(_args(sport="Soccer,Basketball", family="total"))
    b = RUN.subset_slug(_args(sport="Basketball, Soccer", family="total"))
    assert a == b and a.startswith("family=total__sport=Basketball,Soccer__")
    assert a != RUN.subset_slug(_args(sport="Soccer", family="total"))


@pytest.mark.parametrize("stage", ["benchmark", "report", "all"])
def test_filtered_benchmark_report_all_refused(stage):
    with pytest.raises(SystemExit, match="only on the full competition set"):
        RUN.main([stage, "--sport", "Soccer", "--cache-only"])


def test_filtered_evaluate_leaves_full_results_byte_identical(tmp_path, monkeypatch):
    args, comp = _setup_eval(tmp_path, monkeypatch)
    EV.stage_evaluate(args, [comp], lambda m: None)
    full = EV.RESULTS / "child_v"
    before = {p.name: sha256_file(p) for p in full.iterdir()}
    monkeypatch.setattr(RUN, "SUBSETS", tmp_path / "subsets")
    fargs = _args(protocol="child_v", family="game_winner")
    target = RUN.route_outputs(fargs)
    EV.stage_evaluate(SimpleNamespace(protocol="child_v", family="game_winner"), [comp], lambda m: None)
    assert {p.name: sha256_file(p) for p in full.iterdir()} == before
    assert target == tmp_path / "subsets" / RUN.subset_slug(fargs) / "child_v"
    assert (target / "scored.jsonl").exists() and (target / "metrics.json").exists()


# ------------------------------------------------------------------ exact research vs portable docs

def _toy_protocol(tmp_path, monkeypatch):
    for name, sub in (("REPO_ROOT", ""), ("PROTOCOLS", "protocols"), ("PREDICTIONS", "pred"), ("RESULTS", "res"),
                      ("DOCS", "docs")):
        monkeypatch.setattr(MAN, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setitem(MAN.DOC_FILES, "pv", ["a.md"])
    files = [tmp_path / "protocols" / "pv.json", tmp_path / "pred" / "pv" / "c.jsonl"] + \
            [tmp_path / "res" / "pv" / f for f in MAN.RESULT_FILES]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'{"x": 1}\r\n{"y": 2}\r\n')
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_bytes(b"# title\r\nline\r\n")
    MAN.write_manifest("pv", None, "", lambda m: None)


def test_docs_supplement_requires_exact_match_first(tmp_path, monkeypatch):
    _toy_protocol(tmp_path, monkeypatch)
    (tmp_path / "docs" / "a.md").write_bytes(b"# title\nline\n")              # already LF: not the manifest bytes
    with pytest.raises(SystemExit, match="does not match its exact hash"):
        MAN.write_docs_supplement("pv", lambda m: None)


def test_docs_portable_across_newlines_but_research_stays_exact(tmp_path, monkeypatch):
    _toy_protocol(tmp_path, monkeypatch)
    man_before = (tmp_path / "protocols" / "pv.artifacts.json").read_bytes()
    sup = MAN.write_docs_supplement("pv", lambda m: None)
    assert sup["rule"] == "portable_lf_v1" and "why" in sup
    assert (tmp_path / "protocols" / "pv.artifacts.json").read_bytes() == man_before     # original never rewritten
    assert MAN.verify("pv") == []
    doc = tmp_path / "docs" / "a.md"
    doc.write_bytes(b"# title\nline\n")                                       # LF checkout
    assert MAN.verify("pv") == []
    doc.write_bytes(b"# title\r\nline\r\n")                                   # CRLF checkout
    assert MAN.verify("pv") == []
    doc.write_bytes(b"# title\nline changed\n")                               # real content change
    assert any("content mismatch" in p for p in MAN.verify_detail("pv")["docs"])
    doc.write_bytes(b"# title\nline\n")
    m = tmp_path / "res" / "pv" / "metrics.json"
    m.write_bytes(m.read_bytes().replace(b"\r\n", b"\n"))                    # research artifacts: no normalization
    d = MAN.verify_detail("pv")
    assert d["docs"] == [] and any("metrics.json" in p for p in d["research"])


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_real_git_checkouts_verify(tmp_path, monkeypatch, autocrlf):
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "protocols").mkdir()
    (src / ".gitattributes").write_bytes(b"protocols/*.json -text\nres/** -text\n*.md text\n")
    (src / "docs" / "a.md").write_bytes(b"# t\nbody\n")
    (src / "protocols" / "pv.json").write_bytes(b'{"a": 1}\n')
    (src / "res" / "pv").mkdir(parents=True)
    for f in ("scored.jsonl", "metrics.json"):
        (src / "res" / "pv" / f).write_bytes(b'{"r": 1}\n')
    _git("init", "-q", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false", "add", ".", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", cwd=src)
    clones = {}
    for mode in ("true", "false"):
        dst = tmp_path / f"clone_{mode}"
        subprocess.run(["git", "-c", f"core.autocrlf={mode}", "clone", "-q", str(src), str(dst)], check=True)
        clones[mode] = dst
    # manifest assembled on the CRLF (Windows-style) checkout, as the v1/v2 manifests were
    origin = clones["true"]
    assert (origin / "docs" / "a.md").read_bytes() == b"# t\r\nbody\r\n"
    for name, sub in (("REPO_ROOT", ""), ("PROTOCOLS", "protocols"), ("PREDICTIONS", "pred"), ("RESULTS", "res"),
                      ("DOCS", "docs")):
        monkeypatch.setattr(MAN, name, origin / sub if sub else origin)
    monkeypatch.setitem(MAN.DOC_FILES, "pv", ["a.md"])
    monkeypatch.setattr(MAN, "RESULT_FILES", ["scored.jsonl", "metrics.json"])
    MAN.write_manifest("pv", None, "", lambda m: None)
    MAN.write_docs_supplement("pv", lambda m: None)
    target = clones[autocrlf]
    if target != origin:
        for f in ("pv.artifacts.json", "pv.docs_portable_v1.json"):
            shutil.copy(origin / "protocols" / f, target / "protocols" / f)
    for name, sub in (("REPO_ROOT", ""), ("PROTOCOLS", "protocols"), ("PREDICTIONS", "pred"), ("RESULTS", "res"),
                      ("DOCS", "docs")):
        monkeypatch.setattr(MAN, name, target / sub if sub else target)
    assert (target / "protocols" / "pv.json").read_bytes() == b'{"a": 1}\n'     # -text: exact bytes everywhere
    assert MAN.verify("pv") == []


@pytest.mark.parametrize("protocol", ["sports_phase1_v1", "sports_phase1_v2"])
def test_tracked_phase1_docs_verify_in_lf_and_crlf_form(protocol):
    sup = json.loads((REPO / f"research/sports/protocols/{protocol}.docs_portable_v1.json").read_text(encoding="utf-8"))
    man_path = REPO / f"research/sports/protocols/{protocol}.artifacts.json"
    assert sup["original_manifest_sha256"] == sha256_file(man_path)
    for rel, h in sup["files"].items():
        stored = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, check=True, capture_output=True).stdout
        lf = stored.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        assert sha256_bytes(lf) == h["portable_sha256"] == sha256_bytes(crlf.replace(b"\r\n", b"\n")), rel
        assert MAN.portable_sha256(REPO / rel) == h["portable_sha256"], rel
    assert MAN.verify(protocol) == []
