"""Read-only live check of the fitted candidates in accepted WINDOW_CAPTURE records.

For each record the full source verification runs first (checksum, pins, dependencies, raw evidence,
timing), then offline replay. Per contract:

* calibrated == ``apply_calibration(pinned params, frozen p)``; an identity map (a = 0, b = 1) is
  reported because it legitimately equals the frozen candidate (up to the frozen 1e-4 clip).
* blend == ``apply_blend(pinned params, frozen p, market p)``. With model weight w = 0 the blend is
  sigmoid(c + logit(clip(market))): it equals the market only when the intercept c is also 0 (and the
  market is inside the clip range). Zero weight and zero intercept are reported separately.
* market: latest candle ended at or before prediction_as_of, age <= the frozen maximum, two-sided book,
  p is the bid/ask mid.
"""

from __future__ import annotations

import json
from collections import Counter

from ..core import PROTOCOLS
from ..live import config as LC
from ..live import replay as RP
from ..phase2 import calib as CAL
from ..phase2 import config as P2
from . import score as SC

TOL = 1e-12


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL


def blend_shape(params: dict) -> dict:
    w, c = params["w"], params["c"]
    return {"w": w, "c": c, "zero_model_weight": w == 0, "zero_intercept": c == 0,
            "expected_equal_to_market": w == 0 and c == 0}


def check_contract(c: dict, g: dict | None, as_of: float) -> dict:
    cand = c["candidates"]
    out = {"ticker": c["ticker"], "family": c["family"], "problems": [], "available": {}}
    for k in ("frozen", "calibrated", "market", "blend"):
        out["available"][k] = "p" in cand[k]
    fz = cand["frozen"].get("p")
    cal, bl = (g or {}).get("calibration") or {}, (g or {}).get("blend") or {}
    if "p" in cand["calibrated"]:
        if cal.get("status") != "ok":
            out["problems"].append("calibrated present but the pinned map is not ok")
        else:
            prm = cal["params"]
            exp = CAL.apply_calibration(prm, fz)
            out["calibration"] = {"form": cal["selected"]["form"], "identity": prm["a"] == 0 and prm["b"] == 1,
                                  "equals_frozen": _close(cand["calibrated"]["p"], fz)}
            if cand["calibrated"]["p"] != exp:
                out["problems"].append(f"calibrated {cand['calibrated']['p']} != pinned map {exp}")
    elif cal.get("status") == "ok" and fz is not None:
        out["problems"].append("pinned calibration map ok but calibrated unavailable")
    mk = cand["market"]
    if "p" in mk:
        age_max = P2.QUOTE["max_age_hours"] * 3600
        if not mk["candle_end"] <= as_of:
            out["problems"].append("market candle ended after prediction_as_of")
        if not 0 <= mk["age_s"] <= age_max or int(as_of) - mk["candle_end"] != mk["age_s"]:
            out["problems"].append(f"market candle age {mk['age_s']} s violates the frozen rule")
        if not (0 < mk["bid"] <= mk["ask"] < 1) or not _close(mk["p"], (mk["bid"] + mk["ask"]) / 2):
            out["problems"].append("market quote is not a two-sided bid/ask mid")
    if "p" in cand["blend"]:
        if bl.get("status") != "ok" or "p" not in mk or fz is None:
            out["problems"].append("blend present without an ok pinned map and both inputs")
        else:
            exp = CAL.apply_blend(bl["params"], fz, mk["p"])
            out["blend"] = {**blend_shape(bl["params"]), "equals_market": _close(cand["blend"]["p"], mk["p"])}
            if cand["blend"]["p"] != exp:
                out["problems"].append(f"blend {cand['blend']['p']} != pinned map {exp}")
            if out["blend"]["expected_equal_to_market"] and not out["blend"]["equals_market"] \
                    and P2.CLIP < mk["p"] < 1 - P2.CLIP:
                out["problems"].append("w = 0 and c = 0 but blend differs from the market")
    return out


def run(log, competitions: str = "ncaaf") -> int:
    keys = {k.strip() for k in competitions.split(",") if k.strip()}
    groups = json.loads((PROTOCOLS / f"{LC.PARENT_PROTOCOL}.json").read_text(encoding="utf-8"))["groups"]
    eligible, _ = SC.scan()
    recs = [(p, r) for p, r in eligible if r["competition"] in keys]
    if not recs:
        log(f"fitted check: no accepted WINDOW_CAPTURE records for {sorted(keys)} (pending)")
        return 0
    bad, tally = 0, Counter()
    for path, rec in recs:
        try:
            SC.verify_source(path, rec)
            rp = RP.replay_record(rec)
        except Exception as exc:
            bad += 1
            log(f"REFUSED {rec['competition']} {rec['cluster']}: {exc}")
            continue
        if not rp["ok"]:
            bad += 1
            log(f"REPLAY MISMATCH {rec['competition']} {rec['cluster']}: {rp['mismatch']}")
        for c in rec["contracts"]:
            if c["status"] != "ok":
                continue
            res = check_contract(c, groups.get(f"{rec['competition']}|{c['family']}"), rec["prediction_as_of_ts"])
            fam = c["family"]
            for k, v in res["available"].items():
                tally[(fam, f"{k}_available")] += v
            tally[(fam, "contracts")] += 1
            if "calibration" in res:
                tally[(fam, f"calibration_{res['calibration']['form']}")] += 1
                tally[(fam, "calibrated_equals_frozen")] += res["calibration"]["equals_frozen"]
            if "blend" in res:
                b = res["blend"]
                tally[(fam, f"blend_w={b['w']}_c={b['c']:.6g}")] += 1
                tally[(fam, "blend_zero_model_weight")] += b["zero_model_weight"]
                tally[(fam, "blend_zero_intercept")] += b["zero_intercept"]
                tally[(fam, "blend_equals_market")] += b["equals_market"]
            for pr in res["problems"]:
                bad += 1
                log(f"PROBLEM {rec['cluster']} {c['ticker']}: {pr}")
        log(f"{rec['competition']} {rec['cluster']}: verified (pins, evidence, timing) and replay "
            f"{'OK' if rp['ok'] else 'MISMATCH'}; horizon {rec['horizon_minutes']} min")
    for (fam, k), v in sorted(tally.items()):
        log(f"  {fam:<12} {k:<34} {v}")
    log(f"fitted check: {len(recs)} records, {'OK' if not bad else f'{bad} problems'}")
    return 0 if not bad else 1
