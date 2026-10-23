"""ITF singles Elo challenger. Does not import collectors, live.predict, or scoring writers.

Identity is the Kalshi participant UUID. Exact-name collisions are not merged.
Surface is not used. Unseen players keep the Elo base rating.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import models as M
from ..core import sha256_file
from ..engines import Elo, replay
from .constants import CHALLENGER_DIR, PROTOCOL_PATH

CHALLENGER_CODE_FILES = ["itf_singles_v1.py"]
CANDIDATE_ID = "itf_singles_v1"


def code_hash() -> str:
    from ..core import sha256_bytes
    here = Path(__file__).resolve().parent
    return sha256_bytes(b"".join(sha256_file(here / f).encode() for f in CHALLENGER_CODE_FILES))


def load_protocol() -> dict:
    if not PROTOCOL_PATH.is_file():
        raise FileNotFoundError(f"challenger protocol missing: {PROTOCOL_PATH}")
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def selection_rules() -> dict:
    """Recorded before any candidate comparison. Do not edit after a freeze."""
    return {
        "recorded_before_comparison": True,
        "history": "verified frozen normalized events only (capture protocol dependency hashes)",
        "competitions": ["k_kxitfmatch", "k_kxitfwmatch"],
        "identity": "Kalshi participant UUID; exact-name equality does not merge UUIDs",
        "elo_state": "separate per competition (k_kxitfmatch and k_kxitfwmatch never share ratings)",
        "surface": "not used",
        "chronological_split": {
            "select_start": "2026-07-01T00:00:00Z",
            "select_end": "2026-10-01T00:00:00Z",
            "unit": "event start",
            "updates": "available_at <= query cutoff (start - 60 min)",
            "october_21_snapshot": "development data; never used for selection",
        },
        "candidates": {
            "k": [12, 24, 36, 48],
            "carry": [0.0, 0.33],
            "home": [0],
            "mov": [False],
            "gap_days": [120],
            "base": [1500],
        },
        "metric": "mean log loss of P(home wins) on select-window events (clip eps=1e-4)",
        "tie_break": ["lower metric", "lower k", "lower carry"],
        "missing_input": (
            "refuse if frozen history hash mismatches; unseen player uses Elo base 1500; "
            "no independent results; no name merge"
        ),
    }


def _run(events: list[dict], queries: list[tuple], params: dict) -> dict:
    elo = Elo(params["k"], 0, params.get("carry") or 0.0, False, gap_days=params.get("gap_days") or 120,
              base=params.get("base") or 1500.0)
    out = {}

    def upd(e):
        elo.update(e["home"], e["away"], M.ts(e["start"]),
                   1.0 if e["winner_id"] == e["home"] else 0.0, True)

    def pred(q):
        key, a, b, t = q
        out[key] = elo.prob(a, b, t, True)

    replay(M.konly_releases(events), [(q[3] - M.HORIZON_MIN * 60, q) for q in queries], upd, pred)
    return out


def predict(events: list[dict], queries: list[tuple], params: dict) -> dict:
    """queries: (key, participant, opponent, start_ts). Returns key -> p(participant beats opponent)."""
    return _run(events, queries, params)


def select_params(events: list[dict], rules: dict | None = None,
                  pools: list[list[dict]] | None = None) -> dict:
    """Compare candidates only after `rules` is recorded. Snapshot outcomes are not read.

    ``pools`` keeps separate Elo states (men / women). A single ``events`` list is one pool.
    """
    rules = rules or selection_rules()
    t0 = M.ts(rules["chronological_split"]["select_start"])
    t1 = M.ts(rules["chronological_split"]["select_end"])
    groups = pools if pools is not None else [events]
    windows = []
    for group in groups:
        sel = [e for e in group if e.get("start") and e.get("available_at") and e.get("winner_id")
               and t0 <= M.ts(e["start"]) < t1]
        qs = [(e["source_game_id"], e["home"], e["away"], M.ts(e["start"])) for e in sel]
        windows.append((group, sel, qs))
    n_select = sum(len(sel) for _, sel, _ in windows)
    if n_select == 0:
        raise RuntimeError("selection window has no frozen events with start and available_at")
    cand = rules["candidates"]
    ranked = []
    for k in cand["k"]:
        for carry in cand["carry"]:
            prm = {"k": k, "carry": carry, "home": 0, "mov": False,
                   "gap_days": cand["gap_days"][0], "base": cand["base"][0]}
            losses = []
            for group, sel, qs in windows:
                if not sel:
                    continue
                pr = _run(group, qs, prm)
                losses.extend(M.ll(pr[e["source_game_id"]], int(e["winner_id"] == e["home"])) for e in sel)
            metric = sum(losses) / len(losses)
            ranked.append((metric, k, carry, prm, len(losses)))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    best = ranked[0]
    return {
        "params": best[3],
        "select_logloss": best[0],
        "select_events": best[4],
        "n_compared": len(ranked),
        "pools": len(windows),
        "all": [{"k": k, "carry": c, "select_logloss": m, "n": n} for m, k, c, _, n in ranked],
    }


def unavailable(reason: str) -> dict:
    return {"unavailable": reason, "candidate": CANDIDATE_ID}
