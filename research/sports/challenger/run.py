"""Run the ITF study: verify inputs, diagnose, probe sources, freeze one challenger or record a blocker."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from ..core import write_json
from .constants import COMPETITIONS, DOCS, DIAGNOSIS_N_EVENTS, P_AGREE_TOL, PROTOCOL_PATH, STUDY_STEM
from . import diagnose as DG
from . import inputs as IN
from . import itf_singles_v1 as CH
from . import sources as SRC


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or sorted({k for r in rows for k in r})
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv(r.get(k)) for k in fields})


def _csv(v):
    if v is None or v is False and v is not True:
        if v is None:
            return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else ""
    return v


def _agree(a, b, tol=1e-12):
    return {"left": a, "right": b, "abs_diff": abs(a - b), "numerical_agreement": abs(a - b) <= tol}


def decide(diag: dict, source_rows: list[dict], source_usable: dict) -> dict:
    n = diag["n_replay"]
    n_ct = diag["n_capture_time"]
    n_ag = diag["n_agree_capture"]
    agree_frac = n_ag / n if n else 0.0
    parts = { (p["group_by"], p["group"]): p for p in diag["partitions"] }
    n0 = parts.get(("n_bin", "n=0")) or {}
    experienced = math.fsum(
        (parts.get(("n_bin", g)) or {}).get("contrib_event_equal") or 0.0
        for g in ("n=3-9", "n=10-29", "n>=30")
    )
    n0_contrib = n0.get("contrib_event_equal") or 0.0
    if agree_frac < 0.8 or n_ct < 0.8 * n:
        return {
            "kind": "blocker",
            "reason": "incomplete_capture_time_history",
            "detail": (
                f"capture-time history with p agreement within {P_AGREE_TOL} covers "
                f"{n_ag}/{n} matched contracts (capture_time {n_ct}). "
                "Parameter selection from reconstructed history is refused."
            ),
        }
    return {
        "kind": "kalshi_history_challenger",
        "reason": "capture_time_replay_agrees",
        "detail": (
            "Capture-time evidence recovered and replay p agrees with saved frozen p on a large majority "
            "of matched ITF contracts. Challenger uses the same Kalshi UUID history with parameters "
            "selected on the recorded frozen-history split. October 21 outcomes stay development data. "
            f"n0_contrib={n0_contrib!r} experienced_n_ge_3_contrib={experienced!r} "
            "(descriptive slices among capture_time_agree only; not a cause claim). "
            f"Independent ITF feeds passing the usable-feed rule: "
            f"{source_usable.get('usable_itf_result_feeds') or 'none'}. "
            "No reviewed player join exists; exact-name equality does not merge UUIDs, so this "
            "challenger does not ingest those feeds."
        ),
        "agree_frac": agree_frac,
    }


def freeze_challenger(static: dict, selection: dict, freeze_at: str) -> dict:
    rules = CH.selection_rules()
    body = {
        "protocol": "sports_itf_singles_challenger_v1",
        "research_only": True,
        "no_bet": True,
        "scheduler": "none (not connected; future collection is documented only)",
        "candidate_id": CH.CANDIDATE_ID,
        "competitions": list(COMPETITIONS),
        "reference": "sports_phase1_v2 kalshi_only elo (frozen live candidate unchanged)",
        "frozen_at": freeze_at,
        "challenger_code_hash": CH.code_hash(),
        "challenger_code_files": CH.CHALLENGER_CODE_FILES,
        "selection_rules": rules,
        "selected": selection,
        "development_data": (
            "October 21 snapshot ITF matched contracts, and any later replay of events after that "
            "cutoff performed after this freeze, are development analysis. They are not prospective."
        ),
        "prospective": {
            "eligible_after": freeze_at,
            "rule": (
                "Only predictions actually recorded under this protocol before their outcomes "
                "became available qualify as prospective. Replaying events after 2026-10-21 now "
                "is development analysis even if those outcomes have not previously been scored."
            ),
            "future_collection": (
                "A later research task may score this candidate on new WINDOW_CAPTURE records "
                "without connecting the scheduled sports collector."
            ),
        },
        "pins": {
            "phase1_code_hash": static["checks"]["phase1_code_hash"],
            "phase2_code_hash": static["checks"]["phase2_code_hash"],
            "live_code_hash": static["checks"]["live_code_hash"],
            "events_sha256": static["events_sha256"],
            "contracts_sha256": static["contracts_sha256"],
        },
        "identity": "Kalshi participant UUID; name collisions are review candidates and are not merged",
        "surface": "not used",
    }
    write_json(PROTOCOL_PATH, body)
    body["protocol_sha256"] = __import__("research.sports.core", fromlist=["sha256_file"]).sha256_file(PROTOCOL_PATH)
    # re-write including sha of previous body without circular hash: store code hash only
    return body


def write_outputs(static, diag, source_rows, source_usable, decision, protocol, freeze_at) -> None:
    replay_fields = [
        "competition", "ticker", "event_key", "y", "frozen_p", "market_p", "d_brier", "w_event",
        "history_kind", "replay_status", "replay_reason", "agreement_slice", "history_complete",
        "p_replay", "p_abs_diff", "p_agree", "participant", "opponent",
        "n_participant", "n_opponent", "min_n", "n_bin", "seen_participant", "seen_opponent",
        "rating_participant", "rating_opponent", "rating_diff", "abs_d_bin",
        "days_since_participant", "days_since_opponent", "carry_enabled",
        "results_used_match", "as_of_results_used", "saved_results_used",
        "prediction_as_of", "surface_fields",
    ]
    _write_csv(DOCS / f"{STUDY_STEM}_replay.csv", diag["replay_rows"], replay_fields)
    _write_csv(DOCS / f"{STUDY_STEM}_partitions.csv", diag["partitions"])
    _write_csv(DOCS / f"{STUDY_STEM}_identity.csv", diag["identity"])
    _write_csv(DOCS / f"{STUDY_STEM}_sources.csv", source_rows)
    summary = {
        "research_only": True, "no_bet": True, "generated_at": freeze_at,
        "p_agree_tol": P_AGREE_TOL, "diagnosis_n_events": DIAGNOSIS_N_EVENTS,
        "contrib": diag["contrib"],
        "contrib_reconcile": {k: _agree(diag["contrib"][k], diag["expected_contrib"][k], 1e-9)
                              for k in COMPETITIONS},
        "n_replay": diag["n_replay"], "n_capture_time": diag["n_capture_time"],
        "n_agree_capture": diag["n_agree_capture"],
        "event_fields": diag["event_fields"],
        "surface_on_events": diag["surface_on_events"],
        "surface_on_capture_contracts": diag["surface_on_capture_contracts"],
        "structured_targets_loaded": diag["structured_targets_loaded"],
        "identity_counts": _count(diag["identity"], "identity_status"),
        "source_usable": source_usable,
        "decision": {k: v for k, v in decision.items() if k != "detail"} | {"detail": decision["detail"]},
        "protocol": None if protocol is None else {
            "path": "research/sports/challenger/protocol_itf_singles_v1.json",
            "frozen_at": protocol.get("frozen_at"),
            "challenger_code_hash": protocol.get("challenger_code_hash"),
            "selected_params": (protocol.get("selected") or {}).get("params"),
        },
        "static_checks": static["checks"],
    }
    (DOCS / f"{STUDY_STEM}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (DOCS / f"{STUDY_STEM}.md").write_text(
        render_md(static, diag, source_rows, source_usable, decision, protocol, freeze_at, summary),
        encoding="utf-8")


def _count(rows, key):
    out = {}
    for r in rows:
        out[r.get(key) or "unknown"] = out.get(r.get(key) or "unknown", 0) + 1
    return out


def render_md(static, diag, source_rows, source_usable, decision, protocol, freeze_at, summary) -> str:
    rec = summary["contrib_reconcile"]
    ident = summary["identity_counts"]
    n_coll = ident.get("name_collision_review_candidate", 0)
    proto_line = "No challenger protocol was frozen."
    if protocol:
        sel = protocol.get("selected") or {}
        prm = sel.get("params") or {}
        proto_line = (
            f"Challenger protocol `research/sports/challenger/protocol_itf_singles_v1.json` "
            f"frozen_at `{protocol['frozen_at']}` code_hash `{protocol['challenger_code_hash']}`. "
            f"Selected k={prm.get('k')} carry={prm.get('carry')} select_logloss={sel.get('select_logloss')!r} "
            f"on {sel.get('select_events')} frozen events in 2026-07-01–2026-10-01."
        )
    parts_tbl = "\n".join(
        f"| {p['group_by']} | {p['group']} | {p['contracts']} | {p['events']} | {p['contrib_event_equal']!r} | "
        f"{p['p_agree_count']} |"
        for p in diag["partitions"]
    )
    src_tbl = "\n".join(
        f"| {r['source_id']} | {r.get('http_status')} | {r.get('bytes')} | {r.get('content_type')} | "
        f"{r.get('observed_itf_token')} | {r.get('observed_player_id_token')} | {r.get('observed_surface_token')} |"
        for r in source_rows
    )
    return f"""# ITF singles challenger study v1

**As of {freeze_at}.** RESEARCH_ONLY / NO_BET. Descriptive only: no profitability, promotion, or executable-edge claim.

This note continues from the [October 21 forecast-error diagnosis](sports_forecast_error_diagnosis_2026-10-21.md). Frozen models, capture protocols, scoring, collectors, schedules, accepted records, the October 21 review, and weather files were not modified.

Script: `python -m research.sports.challenger.run`. Companion CSVs sit next to this file.

## Verified inputs

Snapshot listing payload sha256 `{static['checks']['listing_payload_sha256']}` (complete listing). Capture protocol `{static['checks']['capture_protocol_sha256']}`. Phase 1 protocol `{static['checks']['phase1_protocol_sha256']}`. Phase 2 protocol `{static['checks']['phase2_protocol_sha256']}`. Phase 1 / Phase 2 / live code hashes match the frozen capture pins. Frozen normalized history used for replay:

- `k_kxitfmatch` events `{static['events_sha256']['k_kxitfmatch']}` contracts `{static['contracts_sha256']['k_kxitfmatch']}`
- `k_kxitfwmatch` events `{static['events_sha256']['k_kxitfwmatch']}` contracts `{static['contracts_sha256']['k_kxitfwmatch']}`

Local normalized files were hashed against those pins before any Elo replay. A mismatch would have stopped the affected replay.

Reconstruction agreement uses documented tolerance `{P_AGREE_TOL}` on `p_replay` versus the saved frozen candidate after mapping each ticker to `elo.prob(participant, opponent)` with participant from the capture record. Query interleaving matches `models._konly_run` (releases by `available_at`, query at start − 60 min, rating time = start).

## ITF totals versus the diagnosis

Event-equal frozen−market Brier contributions use the diagnosis event count N = {DIAGNOSIS_N_EVENTS}:

| Competition | Events (this sample) | Contribution | Diagnosis target | Numerical agreement (1e-9) |
|---|---:|---:|---:|---|
| k_kxitfmatch | {sum(1 for r in diag['replay_rows'] if r['competition']=='k_kxitfmatch')} | {diag['contrib']['k_kxitfmatch']!r} | {diag['expected_contrib']['k_kxitfmatch']!r} | {rec['k_kxitfmatch']['numerical_agreement']} |
| k_kxitfwmatch | {sum(1 for r in diag['replay_rows'] if r['competition']=='k_kxitfwmatch')} | {diag['contrib']['k_kxitfwmatch']!r} | {diag['expected_contrib']['k_kxitfwmatch']!r} | {rec['k_kxitfwmatch']['numerical_agreement']} |

Matched replay rows: **{diag['n_replay']}**. Capture-time history recovered: **{diag['n_capture_time']}**. Capture-time and p-agree: **{diag['n_agree_capture']}**.

## Replay status (read this before history-support slices)

Counts and ratings are **capture-time** only when evidence bodies for that capture verify and `as_of_view.results_used` matches the saved record. Otherwise they are **reconstructed** from frozen normalized events (a lower bound). A reconstructed n=0 does not establish that the live prediction was a cold start. Settlement time alone was not used to add post-freeze results.

| group_by | group | contracts | events | contrib_event_equal | p_agree_count |
|---|---|---:|---:|---:|---:|
{parts_tbl}

History-support (`n_bin`) and rating-separation (`abs_d_bin`) rows in that table are restricted to `capture_time_agree`. They describe replay behavior; they are not a demonstration that k=24 is too small. `carry=0` disables the Elo decay term; it is not shrinkage toward 50%. The 1500 base rating is the unseen-player prior.

## Surface and identity

Frozen event field names observed: `{diag['event_fields']}`. Surface-like fields on those events: `{diag['surface_on_events']}`. Surface-like fields on capture contracts: `{diag['surface_on_capture_contracts']}`. Absent surface is an **input limitation**, not a demonstrated cause of the Brier gap.

Structured targets loaded: {diag['structured_targets_loaded']}. Identity statuses: `{ident}`. **{n_coll}** exact-name collisions are review candidates. They were not merged.

## Free-source probe (observed)

Bounded cached GETs. No paid service. ITF World Tennis Tour coverage is observed from the response, not assumed.

| source_id | HTTP | bytes | content_type | ITF token | player-id token | surface token |
|---|---:|---:|---|---|---|---|
{src_tbl}

Usable ITF result feeds under the study rule (HTTP 200, not HTML-only, ITF token and player-id token both present): **{source_usable['usable_itf_result_feeds'] or 'none'}**.

## Decision

**{decision['kind']}** — `{decision['reason']}`. {decision['detail']}

{proto_line}

October 21 snapshot outcomes remain development data. Replaying any event after `{diag['review_cutoff']}` at study time is also development analysis.

## Prospective rule (starts after this freeze)

Eligible: `k_kxitfmatch` and `k_kxitfwmatch` predictions **recorded under the frozen challenger protocol** with `prediction_as_of` after `{freeze_at}`, made before the outcome was available. Metric: event-equal Brier versus the frozen live candidate and versus the capture market quote. No new significance gate. RESEARCH_ONLY / NO_BET.

Future collection: a later research task may attach this candidate to new WINDOW_CAPTURE records. This task does not connect a scheduler and does not change the sports collector.

## Next step

If a challenger is frozen, collect prospective records after `{freeze_at}` without changing the scheduled collector. If the decision is a blocker, the next step is a reviewed player join or a newly verified ITF results feed — not parameter search on reconstructed n counts.
"""


def main() -> int:
    freeze_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    static = IN.verify_static_inputs()
    diag = DG.diagnose(static)
    source_rows = SRC.probe()
    source_usable = SRC.usable_itf_results(source_rows)
    decision = decide(diag, source_rows, source_usable)
    protocol = None
    if decision["kind"] == "kalshi_history_challenger":
        rules = CH.selection_rules()
        pools = [DG.load_frozen_events(key) for key in COMPETITIONS]
        selection = CH.select_params([], rules, pools=pools)
        protocol = freeze_challenger(static, selection, freeze_at)
        sel = selection["params"]
        if sel["k"] == 24 and float(sel["carry"]) == 0.0:
            PROTOCOL_PATH.unlink(missing_ok=True)
            protocol = None
            decision = {
                "kind": "blocker",
                "reason": "selection_recovered_frozen_defaults",
                "detail": (
                    "Capture-time replay agreed, but the recorded grid selected k=24 carry=0 on the "
                    "frozen 2026-07-01–2026-10-01 window — the same defaults as the live frozen model. "
                    "No separate challenger was frozen."
                ),
                "selection": selection["all"],
            }
    write_outputs(static, diag, source_rows, source_usable, decision, protocol, freeze_at)
    print(json.dumps({"decision": decision["kind"], "reason": decision["reason"],
                      "n_replay": diag["n_replay"], "n_agree_capture": diag["n_agree_capture"],
                      "protocol": None if protocol is None else protocol.get("frozen_at")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
