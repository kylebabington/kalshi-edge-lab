"""Read-only sports forecast-error diagnosis on the frozen 2026-10-21 review snapshot.

Does not fetch, retune, write capture/score records, or import scoring writers.
Reporters from the October 21 review are not re-run against live directories.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.sports import evaluate as EV  # noqa: E402
from research.sports.core import PROTOCOLS, sha256_file  # noqa: E402
from research.sports.scoring import score as SC  # noqa: E402

SNAP = REPO / "data" / "results" / "prospective_review_asof"
DOCS = REPO / "docs" / "research"
PREFIX = "sports_forecast_error_diagnosis_2026-10-21"
LISTING_GZ = DOCS / "prospective_performance_review_2026-10-21_inputs.tsv.gz"
LISTING_SHA256 = "53abb22116a0a4194c92097975387914a02d4747ed95a42f4c38a4edce1a51c8"
PHASE2_PATH = PROTOCOLS / "sports_phase2_v1.json"
PHASE2_SHA256 = "b95e662b233931caafbedfa16b04081d3c9ca01a2e43abfafa0c81867c81cb39"
TARGET_EVENT_BRIER = 0.032064331771587344
SEED = 20261021
N_POS, N_NEG, N_RAND = 10, 10, 20
ECE_BINS = EV.ECE_BINS
TOL = 1e-12
ESSENTIAL = ("ticker", "event_key", "sport", "competition", "family", "y")

SCORE_LIVE_PREFIX = "data/sports/phase2/capture_v2_scores/"
CAP_LIVE_PREFIX = "data/sports/phase2/capture_v2/"


class DiagnosisError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_dt(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_listing() -> dict[str, tuple[str, int]]:
    raw = gzip.decompress(LISTING_GZ.read_bytes())
    got = sha256_bytes(raw)
    if got != LISTING_SHA256:
        raise DiagnosisError(f"listing gz payload sha256 {got} != {LISTING_SHA256}")
    out = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        path, digest, size_s = line.decode("utf-8").split("\t")
        out[path.replace("\\", "/")] = (digest, int(size_s))
    return out


def snap_for(live_rel: str) -> Path | None:
    rel = live_rel.replace("\\", "/")
    if rel.startswith(SCORE_LIVE_PREFIX):
        return SNAP / "sports_scores" / rel[len(SCORE_LIVE_PREFIX):]
    if rel.startswith(CAP_LIVE_PREFIX):
        return SNAP / "sports_capture_v2" / rel[len(CAP_LIVE_PREFIX):]
    return None


def read_verified(live_rel: str, listing: dict[str, tuple[str, int]]) -> tuple[bytes, dict]:
    rel = live_rel.replace("\\", "/")
    if rel not in listing:
        raise DiagnosisError(f"not in frozen listing: {rel}")
    path = snap_for(rel)
    if path is None or not path.is_file():
        raise DiagnosisError(f"snapshot file missing for {rel}")
    data = path.read_bytes()
    want, size = listing[rel]
    if len(data) != size or sha256_bytes(data) != want:
        raise DiagnosisError(f"snapshot bytes differ from listing for {rel}")
    try:
        obj = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise DiagnosisError(f"unreadable JSON {rel}: {exc}") from exc
    return data, obj


def cand_p(block) -> float | None:
    if not isinstance(block, dict) or "p" not in block:
        return None
    p = block["p"]
    if not SC.valid_p(p):
        raise DiagnosisError(f"invalid probability {p!r}")
    return float(p)


def bin_index(p: float) -> int:
    return min(ECE_BINS - 1, max(0, int(p * ECE_BINS)))


def bin_label(i: int) -> str:
    lo = i / ECE_BINS
    hi = (i + 1) / ECE_BINS
    return f"[{lo:.1f},{hi:.1f}]" if i < ECE_BINS - 1 else f"[{lo:.1f},{hi:.1f}]"


def agree(a: float, b: float) -> dict:
    diff = a - b
    return {"left": a, "right": b, "abs_diff": abs(diff),
            "numerical_agreement": abs(diff) <= TOL, "bit_identical": a == b}


def load_matched(listing: dict) -> list[dict]:
    root = SNAP / "sports_scores" / "finalized"
    if not root.is_dir():
        raise DiagnosisError(f"snapshot scores missing: {root}")
    rows = []
    n_files = 0
    for p in sorted(root.glob("*/*/*.json")):
        n_files += 1
        live = "data/sports/phase2/capture_v2_scores/" + p.relative_to(SNAP / "sports_scores").as_posix()
        _, s = read_verified(live, listing)
        if s.get("y") is None:
            continue
        fz = cand_p((s.get("candidates") or {}).get("frozen"))
        mk = cand_p((s.get("candidates") or {}).get("market"))
        if fz is None or mk is None:
            continue
        missing = [k for k in ESSENTIAL if s.get(k) is None or s.get(k) == ""]
        if missing:
            raise DiagnosisError(f"{s.get('ticker')}: missing essential fields {missing}")
        if s["y"] not in (0, 1):
            raise DiagnosisError(f"{s['ticker']}: y={s['y']!r} is not a binary label")
        lf = EV._losses({"p": fz, "y": s["y"]})
        lm = EV._losses({"p": mk, "y": s["y"]})
        rows.append({
            "ticker": s["ticker"], "event_key": s["event_key"], "sport": str(s["sport"]),
            "competition": str(s["competition"]), "family": str(s["family"]),
            "cluster": s.get("cluster"), "side": s.get("side"), "strike": s.get("strike"),
            "cohort": s.get("cohort"), "series": s.get("series"),
            "y": int(s["y"]), "frozen_p": fz, "market_p": mk,
            "calibrated_p": cand_p((s.get("candidates") or {}).get("calibrated")),
            "blend_p": cand_p((s.get("candidates") or {}).get("blend")),
            "d_brier": lf[0] - lm[0], "d_log": lf[1] - lm[1],
            "frozen_brier": lf[0], "market_brier": lm[0],
            "frozen_log": lf[1], "market_log": lm[1],
            "horizon_minutes": s.get("horizon_minutes"),
            "prediction_as_of": s.get("prediction_as_of"),
            "scheduled_cutoff": s.get("scheduled_cutoff"),
            "source_record": s.get("source_record"),
            "source_record_sha256": s.get("source_record_sha256"),
            "settlement_state": (s.get("settlement") or {}).get("state"),
            "market": (s.get("candidates") or {}).get("market") or {},
            "calibrated": (s.get("candidates") or {}).get("calibrated") or {},
            "blend": (s.get("candidates") or {}).get("blend") or {},
        })
    if not rows:
        raise DiagnosisError("no frozen-market matched labelled scores in the snapshot")
    n_e = defaultdict(int)
    for r in rows:
        n_e[r["event_key"]] += 1
    n_events = len(n_e)
    for r in rows:
        r["n_e"] = n_e[r["event_key"]]
        r["w_event"] = 1.0 / (n_events * r["n_e"])
        r["w_contract"] = 1.0 / len(rows)
    rows.sort(key=lambda r: (r["event_key"], r["ticker"]))
    return rows


def event_means(rows: list[dict], key: str) -> dict[str, float]:
    acc = defaultdict(lambda: [0.0, 0])
    for r in rows:
        acc[r["event_key"]][0] += r[key]
        acc[r["event_key"]][1] += 1
    return {e: tot / n for e, (tot, n) in acc.items()}


def partition_contrib(rows: list[dict], group_key: str, value_key: str) -> list[dict]:
    """Event-partitioned contribution: each event belongs to one group. Sums to the event-equal mean."""
    em = event_means(rows, value_key)
    n = len(em)
    by = defaultdict(list)
    contracts = defaultdict(int)
    for r in rows:
        by[r[group_key]].append(r["event_key"])
        contracts[r[group_key]] += 1
    out = []
    for g in sorted(by):
        evs = sorted(set(by[g]))
        means = [em[e] for e in evs]
        contrib = math.fsum(means) / n
        out.append({
            "group_by": group_key, "group": g, "kind": "event_partition",
            "contracts": contracts[g], "events": len(evs),
            "mean_event": math.fsum(means) / len(means),
            "mean_contract": math.fsum(r[value_key] for r in rows if r[group_key] == g) / contracts[g],
            "contrib_event_equal": contrib, "value": value_key,
        })
    return out


def family_share(rows: list[dict], value_key: str) -> list[dict]:
    """Within-event contract share of each event mean, then averaged over events. Additive."""
    n = len({r["event_key"] for r in rows})
    by_e = defaultdict(list)
    for r in rows:
        by_e[r["event_key"]].append(r)
    fams = sorted({r["family"] for r in rows})
    out = []
    for fam in fams:
        pieces = []
        n_contracts = 0
        events_with = set()
        for e, rs in by_e.items():
            n_e = len(rs)
            s = math.fsum(r[value_key] for r in rs if r["family"] == fam)
            k = sum(1 for r in rs if r["family"] == fam)
            if k:
                events_with.add(e)
                n_contracts += k
            pieces.append(s / n_e)
        means_restricted = []
        for e in events_with:
            fam_rows = [r for r in by_e[e] if r["family"] == fam]
            means_restricted.append(math.fsum(r[value_key] for r in fam_rows) / len(fam_rows))
        out.append({
            "group_by": "family", "group": fam, "kind": "within_event_share",
            "contracts": n_contracts, "events": len(events_with),
            "mean_event_restricted": (math.fsum(means_restricted) / len(means_restricted)
                                      if means_restricted else None),
            "mean_contract": (math.fsum(r[value_key] for r in rows if r["family"] == fam) / n_contracts
                              if n_contracts else None),
            "contrib_event_equal": math.fsum(pieces) / n, "value": value_key,
        })
    return out


def weighted_mean(rows, value, weight) -> float:
    return math.fsum(r[value] * r[weight] for r in rows)


def bin_table(rows: list[dict], p_key: str, weight: str) -> list[dict]:
    """Calibration bins. Loss shares use the same weights and sum to the weighted aggregates."""
    buckets = [[] for _ in range(ECE_BINS)]
    for r in rows:
        buckets[bin_index(r[p_key])].append(r)
    wsum = math.fsum(r[weight] for r in rows)
    out = []
    for i, rs in enumerate(buckets):
        ww = math.fsum(r[weight] for r in rs) if rs else 0.0
        events = sorted({r["event_key"] for r in rs})
        row = {
            "p_source": p_key, "weighting": weight, "bin": i, "bin_label": bin_label(i),
            "contracts": len(rs), "events": len(events), "weight_sum": ww,
        }
        if not rs or ww == 0:
            row.update({k: None for k in (
                "mean_p", "observed_freq", "share_d_brier", "share_d_log",
                "share_frozen_brier", "share_market_brier", "share_frozen_log", "share_market_log",
                "extreme_misses")})
        else:
            row["mean_p"] = math.fsum(r[p_key] * r[weight] for r in rs) / ww
            row["observed_freq"] = math.fsum(r["y"] * r[weight] for r in rs) / ww
            row["share_d_brier"] = math.fsum(r["d_brier"] * r[weight] for r in rs)
            row["share_d_log"] = math.fsum(r["d_log"] * r[weight] for r in rs)
            row["share_frozen_brier"] = math.fsum(r["frozen_brier"] * r[weight] for r in rs)
            row["share_market_brier"] = math.fsum(r["market_brier"] * r[weight] for r in rs)
            row["share_frozen_log"] = math.fsum(r["frozen_log"] * r[weight] for r in rs)
            row["share_market_log"] = math.fsum(r["market_log"] * r[weight] for r in rs)
            row["extreme_misses"] = sum(
                1 for r in rs
                if (r[p_key] < 0.05 and r["y"] == 1) or (r[p_key] > 0.95 and r["y"] == 0)
            )
            row["overconfidence"] = row["mean_p"] - row["observed_freq"]
        row["weight_sum_all"] = wsum
        out.append(row)
    return out


def freeze_sample(rows: list[dict]) -> list[dict]:
    """Select cases from deltas and tickers only. No capture-record inspection yet."""
    by_ticker = {r["ticker"]: r for r in rows}
    pos = sorted(rows, key=lambda r: (-r["d_brier"], r["ticker"]))[:N_POS]
    neg = sorted(rows, key=lambda r: (r["d_brier"], r["ticker"]))[:N_NEG]
    rng = random.Random(SEED)
    tickers = [r["ticker"] for r in rows]
    rng.shuffle(tickers)
    rand = []
    for t in tickers:
        if len(rand) >= N_RAND:
            break
        rand.append(by_ticker[t])
    selected: dict[str, dict] = {}
    order = []
    def add(r, reason):
        if r["ticker"] not in selected:
            selected[r["ticker"]] = {**r, "selection_reasons": []}
            order.append(r["ticker"])
        if reason not in selected[r["ticker"]]["selection_reasons"]:
            selected[r["ticker"]]["selection_reasons"].append(reason)
    for r in pos:
        add(r, "largest_positive_d_brier")
    for r in neg:
        add(r, "largest_negative_d_brier")
    for r in rand:
        add(r, f"random_seed_{SEED}")
    return [selected[t] for t in order]


def inspect_cases(sample: list[dict], listing: dict) -> list[dict]:
    out = []
    for r in sample:
        finding = {
            "ticker": r["ticker"], "event_key": r["event_key"], "sport": r["sport"],
            "competition": r["competition"], "family": r["family"], "side": r.get("side"),
            "strike": r.get("strike"), "y": r["y"], "frozen_p": r["frozen_p"],
            "market_p": r["market_p"], "d_brier": r["d_brier"], "d_log": r["d_log"],
            "horizon_minutes": r.get("horizon_minutes"),
            "prediction_as_of": r.get("prediction_as_of"),
            "settlement_state": r.get("settlement_state"),
            "selection_reasons": ",".join(r["selection_reasons"]),
            "classification": "ordinary_incorrect_prediction",
            "notes": [],
        }
        mk = r.get("market") or {}
        finding["market_candle_end"] = mk.get("candle_end")
        finding["market_age_s"] = mk.get("age_s")
        finding["market_bid"] = mk.get("bid")
        finding["market_ask"] = mk.get("ask")
        as_of = parse_dt(r.get("prediction_as_of"))
        cend = mk.get("candle_end")
        candle_dt = None
        if isinstance(cend, (int, float)):
            candle_dt = datetime.fromtimestamp(cend, timezone.utc)
        elif isinstance(cend, str):
            candle_dt = parse_dt(cend)
        if as_of is not None and candle_dt is not None:
            if candle_dt > as_of:
                finding["classification"] = "confirmed_defect"
                finding["notes"].append("market candle_end is after prediction_as_of")
            elif mk.get("age_s") is not None:
                expected = int(as_of.timestamp()) - int(candle_dt.timestamp())
                if int(mk["age_s"]) != expected:
                    finding["classification"] = "confirmed_defect"
                    finding["notes"].append(
                        f"market age_s {mk['age_s']} != prediction_as_of - candle_end ({expected})")
        else:
            finding["notes"].append("cannot verify candle vs prediction_as_of: missing parseable timestamps")
            if finding["classification"] != "confirmed_defect":
                finding["classification"] = "cannot_verify"
        if mk.get("bid") is not None and mk.get("ask") is not None:
            bid, ask, mid = mk["bid"], mk["ask"], r["market_p"]
            if not (0 < bid <= ask < 1) or abs(mid - (bid + ask) / 2) > 1e-12:
                finding["classification"] = "confirmed_defect"
                finding["notes"].append("market p is not a two-sided bid/ask mid")
        src = r.get("source_record")
        if not src:
            finding["notes"].append("cannot verify capture record: source_record missing")
            if finding["classification"] != "confirmed_defect":
                finding["classification"] = "cannot_verify"
            out.append(finding)
            continue
        try:
            _, rec = read_verified(src.replace("\\", "/"), listing)
        except DiagnosisError as exc:
            finding["notes"].append(f"cannot verify capture record: {exc}")
            if finding["classification"] != "confirmed_defect":
                finding["classification"] = "cannot_verify"
            out.append(finding)
            continue
        want_sha = r.get("source_record_sha256")
        rec_sha = rec.get("record_sha256")
        if want_sha and rec_sha and want_sha != rec_sha:
            finding["classification"] = "confirmed_defect"
            finding["notes"].append("score source_record_sha256 does not match snapshot record")
        view = rec.get("as_of_view")
        finding["as_of_view"] = view
        finding["notes"].append(
            "as_of_view on the saved record is a summary (results_used / events_hidden / "
            "latest_release_used). It cannot prove every unavailable outcome field was cleared; "
            "that rule lives in research.sports.live.sources.hide and is not re-checked here."
        )
        contract = next((c for c in rec.get("contracts") or [] if c.get("ticker") == r["ticker"]), None)
        if contract is None:
            finding["notes"].append("cannot verify participant/side: ticker absent from snapshot record contracts")
            if finding["classification"] != "confirmed_defect":
                finding["classification"] = "cannot_verify"
        else:
            finding["participant"] = contract.get("participant")
            finding["record_side"] = contract.get("side")
            finding["record_family"] = contract.get("family")
            if contract.get("side") is not None and r.get("side") is not None and contract["side"] != r["side"]:
                finding["classification"] = "confirmed_defect"
                finding["notes"].append("score.side differs from capture-record contract.side")
            if not contract.get("participant") and r["family"] == "game_winner":
                finding["notes"].append("cannot verify team identity: participant not stored on this contract")
                if finding["classification"] != "confirmed_defect":
                    finding["classification"] = "cannot_verify"
        wo, cut = rec.get("window_open"), rec.get("scheduled_cutoff")
        asof, fin = rec.get("prediction_as_of"), rec.get("finalized_at")
        ts = [parse_dt(x) for x in (wo, asof, fin, cut)]
        if any(x is None for x in ts):
            finding["notes"].append("cannot verify window timing: a capture timestamp is missing or unparseable")
            if finding["classification"] != "confirmed_defect":
                finding["classification"] = "cannot_verify"
        elif not (ts[0] <= ts[1] <= ts[2] <= ts[3]):
            finding["classification"] = "confirmed_defect"
            finding["notes"].append("window timing invariant fails on the saved record")
        if finding["classification"] == "ordinary_incorrect_prediction" and not finding["notes"]:
            finding["notes"].append("no confirmed input defect on saved score/record fields that this check covers")
        elif finding["classification"] == "ordinary_incorrect_prediction":
            finding["notes"].append(
                "no confirmed input defect; remaining notes are verification limits, not corrections"
            )
        out.append(finding)
    return out


def protocol_maps() -> tuple[str, list[dict]]:
    digest = sha256_file(PHASE2_PATH)
    if digest != PHASE2_SHA256:
        raise DiagnosisError(f"sports_phase2_v1.json sha256 {digest} != pinned {PHASE2_SHA256}")
    proto = json.loads(PHASE2_PATH.read_text(encoding="utf-8"))
    groups = proto.get("groups") or {}
    rows = []
    for key, g in groups.items():
        cal, bl = g.get("calibration") or {}, g.get("blend") or {}
        row = {
            "group": key, "competition": g.get("competition"), "family": g.get("family"),
            "sport": g.get("sport"),
            "calibration_status": cal.get("status"), "blend_status": bl.get("status"),
        }
        if cal.get("status") == "ok":
            sel, prm = cal.get("selected") or {}, cal.get("params") or {}
            row.update({
                "cal_form": sel.get("form"), "cal_lambda": sel.get("lambda"),
                "cal_a": prm.get("a"), "cal_b": prm.get("b"),
                "cal_class": ("identity" if sel.get("form") == "identity"
                              or (prm.get("a") == 0 and prm.get("b") == 1)
                              else "non_identity"),
            })
        if bl.get("status") == "ok":
            sel, prm = bl.get("selected") or {}, bl.get("params") or {}
            w, c = prm.get("w"), prm.get("c")
            if w == 0 and c == 0:
                klass = "market_copy_w0_c0"
            elif w == 0:
                klass = "market_only_transform_w0"
            else:
                klass = "nonzero_model_weight"
            row.update({
                "blend_w": w, "blend_c": c, "blend_lambda": prm.get("lambda"),
                "blend_selected_w": sel.get("w"), "blend_selected_lambda": sel.get("lambda"),
                "blend_class": klass,
            })
        if cal.get("status") == "ok" or bl.get("status") == "ok":
            rows.append(row)
    return digest, rows


def fitted_slices(rows: list[dict], maps: list[dict]) -> list[dict]:
    by = {m["group"]: m for m in maps}
    cal_rows = [r for r in rows if r["calibrated_p"] is not None]
    blend_rows = [r for r in rows if r["blend_p"] is not None]
    out = []

    def slice_pair(subset, a_p, b_p, label, klass):
        if not subset:
            return
        per = defaultdict(list)
        for r in subset:
            la = EV._losses({"p": r[a_p], "y": r["y"]})
            lb = EV._losses({"p": r[b_p], "y": r["y"]})
            per[r["event_key"]].append((la[0] - lb[0], la[1] - lb[1]))
        flat = [d for v in per.values() for d in v]
        ev = [(math.fsum(x[0] for x in v) / len(v), math.fsum(x[1] for x in v) / len(v)) for v in per.values()]
        out.append({
            "sample": label, "map_class": klass, "pair": f"{a_p}-{b_p}",
            "contracts": len(flat), "events": len(per),
            "brier_contract": math.fsum(d[0] for d in flat) / len(flat),
            "brier_event": math.fsum(e[0] for e in ev) / len(ev),
            "log_loss_contract": math.fsum(d[1] for d in flat) / len(flat),
            "log_loss_event": math.fsum(e[1] for e in ev) / len(ev),
        })

    for klass in ("identity", "non_identity"):
        keys = {m["group"] for m in maps if m.get("cal_class") == klass}
        sub = [r for r in cal_rows if f"{r['competition']}|{r['family']}" in keys]
        slice_pair(sub, "calibrated_p", "frozen_p", "calibrated_vs_frozen", klass)
    for klass in ("market_copy_w0_c0", "market_only_transform_w0", "nonzero_model_weight"):
        keys = {m["group"] for m in maps if m.get("blend_class") == klass}
        sub = [r for r in blend_rows if f"{r['competition']}|{r['family']}" in keys]
        slice_pair(sub, "blend_p", "frozen_p", "blend_vs_frozen", klass)
        slice_pair(sub, "blend_p", "market_p", "blend_vs_market", klass)
    # groups actually present
    present = defaultdict(lambda: {"cal_events": set(), "blend_events": set(), "cal_n": 0, "blend_n": 0})
    for r in cal_rows:
        g = by.get(f"{r['competition']}|{r['family']}")
        if g:
            present[g["group"]]["cal_events"].add(r["event_key"])
            present[g["group"]]["cal_n"] += 1
    for r in blend_rows:
        g = by.get(f"{r['competition']}|{r['family']}")
        if g:
            present[g["group"]]["blend_events"].add(r["event_key"])
            present[g["group"]]["blend_n"] += 1
    for m in maps:
        p = present.get(m["group"]) or {}
        out.append({
            "sample": "group_coverage", "map_class": m.get("cal_class") or m.get("blend_class"),
            "pair": m["group"], "contracts": p.get("cal_n", 0) + p.get("blend_n", 0),
            "events": len((p.get("cal_events") or set()) | (p.get("blend_events") or set())),
            "calibrated_contracts": p.get("cal_n", 0),
            "calibrated_events": len(p.get("cal_events") or []),
            "blend_contracts": p.get("blend_n", 0),
            "blend_events": len(p.get("blend_events") or []),
            **{k: m.get(k) for k in (
                "competition", "family", "sport", "cal_form", "cal_lambda", "cal_a", "cal_b",
                "cal_class", "blend_w", "blend_c", "blend_lambda", "blend_class")},
        })
    return out


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.write_bytes(b"")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            out = {}
            for k in fields:
                v = r.get(k)
                if isinstance(v, float):
                    out[k] = repr(v) if k in ("cal_a", "cal_b", "blend_w", "blend_c", "frozen_p",
                                              "market_p", "d_brier", "d_log") else f"{v:.12g}"
                elif isinstance(v, list):
                    out[k] = json.dumps(v)
                else:
                    out[k] = v
            w.writerow(out)


def horizon_summary(rows: list[dict]) -> dict:
    hs = [r["horizon_minutes"] for r in rows if isinstance(r.get("horizon_minutes"), (int, float))]
    ages = []
    missing_h = sum(1 for r in rows if r.get("horizon_minutes") is None)
    missing_age = 0
    for r in rows:
        a = (r.get("market") or {}).get("age_s")
        if a is None:
            missing_age += 1
        else:
            ages.append(float(a))
    def stats(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return {"n": len(xs), "min": xs[0], "median": xs[len(xs)//2], "max": xs[-1],
                "mean": math.fsum(xs) / len(xs)}
    return {"horizon_minutes": stats(hs), "missing_horizon": missing_h,
            "market_age_s": stats(ages), "missing_market_age_s": missing_age}


def main() -> int:
    listing = load_listing()
    phase2_sha, maps = protocol_maps()
    rows = load_matched(listing)
    n, n_ev = len(rows), len({r["event_key"] for r in rows})
    d_brier_event = weighted_mean(rows, "d_brier", "w_event")
    d_brier_contract = weighted_mean(rows, "d_brier", "w_contract")
    d_log_event = weighted_mean(rows, "d_log", "w_event")
    d_log_contract = weighted_mean(rows, "d_log", "w_contract")
    frozen_brier_event = weighted_mean(rows, "frozen_brier", "w_event")
    market_brier_event = weighted_mean(rows, "market_brier", "w_event")

    sport = partition_contrib(rows, "sport", "d_brier")
    sport_log = partition_contrib(rows, "sport", "d_log")
    comp = partition_contrib(rows, "competition", "d_brier")
    fam = family_share(rows, "d_brier")
    fam_log = family_share(rows, "d_log")

    recon = {
        "matched_contracts": n, "matched_events": n_ev,
        "d_brier_event": d_brier_event, "d_brier_contract": d_brier_contract,
        "d_log_event": d_log_event, "d_log_contract": d_log_contract,
        "vs_frozen_review_event_brier": agree(d_brier_event, TARGET_EVENT_BRIER),
        "sport_contrib_sum_d_brier": agree(math.fsum(r["contrib_event_equal"] for r in sport), d_brier_event),
        "competition_contrib_sum_d_brier": agree(math.fsum(r["contrib_event_equal"] for r in comp), d_brier_event),
        "family_share_sum_d_brier": agree(math.fsum(r["contrib_event_equal"] for r in fam), d_brier_event),
        "sport_contrib_sum_d_log": agree(math.fsum(r["contrib_event_equal"] for r in sport_log), d_log_event),
        "weight_event_sum": agree(math.fsum(r["w_event"] for r in rows), 1.0),
        "weight_contract_sum": agree(math.fsum(r["w_contract"] for r in rows), 1.0),
    }
    for k, v in recon.items():
        if isinstance(v, dict) and not v["numerical_agreement"]:
            raise DiagnosisError(f"reconciliation failed: {k} {v}")

    bins = []
    for p_key in ("frozen_p", "market_p"):
        for w in ("w_contract", "w_event"):
            bins.extend(bin_table(rows, p_key, w))
    # bin shares must sum to the matching aggregate
    for p_key in ("frozen_p", "market_p"):
        for w, target in (("w_contract", d_brier_contract), ("w_event", d_brier_event)):
            sub = [b for b in bins if b["p_source"] == p_key and b["weighting"] == w]
            s = math.fsum(b["share_d_brier"] or 0.0 for b in sub)
            chk = agree(s, target)
            recon[f"bins_{p_key}_{w}_share_d_brier"] = chk
            if not chk["numerical_agreement"]:
                raise DiagnosisError(f"bin share reconciliation failed: {p_key} {w} {chk}")
            fb = math.fsum(b["share_frozen_brier"] or 0.0 for b in sub)
            recon[f"bins_{p_key}_{w}_share_frozen_brier"] = agree(
                fb, weighted_mean(rows, "frozen_brier", w))

    sample = freeze_sample(rows)
    cases = inspect_cases(sample, listing)
    fitted = fitted_slices(rows, maps)
    horizons = horizon_summary(rows)

    contrib_rows = sport + sport_log + comp + partition_contrib(rows, "competition", "d_log") + fam + fam_log
    write_csv(DOCS / f"{PREFIX}_contributions.csv", contrib_rows,
              ["value", "group_by", "group", "kind", "contracts", "events", "mean_event",
               "mean_event_restricted", "mean_contract", "contrib_event_equal"])
    write_csv(DOCS / f"{PREFIX}_bins.csv", bins,
              ["p_source", "weighting", "bin", "bin_label", "contracts", "events", "weight_sum",
               "mean_p", "observed_freq", "overconfidence", "share_d_brier", "share_d_log",
               "share_frozen_brier", "share_market_brier", "share_frozen_log", "share_market_log",
               "extreme_misses"])
    write_csv(DOCS / f"{PREFIX}_cases.csv", cases,
              ["ticker", "event_key", "sport", "competition", "family", "side", "record_side",
               "participant", "strike", "y", "frozen_p", "market_p", "d_brier", "d_log",
               "horizon_minutes", "prediction_as_of", "market_candle_end", "market_age_s",
               "market_bid", "market_ask", "settlement_state", "selection_reasons",
               "classification", "notes"])
    write_csv(DOCS / f"{PREFIX}_fitted_maps.csv", maps + fitted,
              ["group", "competition", "family", "sport", "calibration_status", "blend_status",
               "cal_form", "cal_lambda", "cal_a", "cal_b", "cal_class", "blend_w", "blend_c",
               "blend_lambda", "blend_selected_w", "blend_selected_lambda", "blend_class",
               "sample", "map_class", "pair", "contracts", "events", "brier_contract",
               "brier_event", "log_loss_contract", "log_loss_event", "calibrated_contracts",
               "calibrated_events", "blend_contracts", "blend_events"])
    summary = {
        "cutoff_utc": "2026-10-21T20:38:39.935499+00:00",
        "snapshot": str(SNAP), "listing_sha256": LISTING_SHA256,
        "phase2_protocol": str(PHASE2_PATH.relative_to(REPO)).replace("\\", "/"),
        "phase2_protocol_sha256": phase2_sha,
        "random_seed": SEED, "ece_bins": ECE_BINS, "tolerance": TOL,
        "aggregates": {
            "contracts": n, "events": n_ev,
            "d_brier_event": d_brier_event, "d_brier_contract": d_brier_contract,
            "d_log_event": d_log_event, "d_log_contract": d_log_contract,
            "frozen_brier_event": frozen_brier_event, "market_brier_event": market_brier_event,
        },
        "reconciliation": recon, "horizons": horizons,
        "case_classifications": {k: sum(1 for c in cases if c["classification"] == k)
                                 for k in ("confirmed_defect", "ordinary_incorrect_prediction", "cannot_verify")},
        "ok_calibration_groups": [m["group"] for m in maps if m.get("calibration_status") == "ok"],
        "ok_blend_groups": [m["group"] for m in maps if m.get("blend_status") == "ok"],
    }
    (DOCS / f"{PREFIX}_summary.json").write_bytes(
        (json.dumps(summary, indent=2, default=str) + "\n").encode("utf-8"))
    print(json.dumps({
        "contracts": n, "events": n_ev, "d_brier_event": d_brier_event,
        "vs_review": recon["vs_frozen_review_event_brier"],
        "sport_top": sorted(sport, key=lambda r: -abs(r["contrib_event_equal"]))[:5],
        "family_shares": fam, "cases": recon and summary["case_classifications"],
        "maps": summary["ok_calibration_groups"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
