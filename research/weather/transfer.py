"""Settlement target-transfer assessment (NWS/KNYC → TWC/CLINYC).

Identity transfer means: predicted CLINYC = predicted KNYC (no offset).

Development pair metrics motivate the candidate rule. They do NOT certify
``settlement_source_transfer_validated``. Only the frozen prospective
hypothesis may flip that flag.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from kalshi import cache
from research.weather.models import (
    EVIDENCE_CLASS_DEVELOPMENT,
    EVIDENCE_CLASS_PROSPECTIVE,
    HYPOTHESIS_ID_CLINYC_TRANSFER_V1,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    STANDARDIZED_RUNS,
    TRANSFER_STATUS_EXPERIMENTAL,
    TRANSFER_STATUS_VALIDATED,
)


HYPOTHESES_DIR = cache.REPO_ROOT / "research" / "weather" / "hypotheses"
HYPOTHESIS_PATH = HYPOTHESES_DIR / "clinyc_identity_transfer.json"
TRANSFER_REPORT_PATH = cache.RESULTS_ROOT / "settlement_transfer_assessment.json"

# Clopper–Pearson / Jeffreys-style one-sided upper bound via Beta quantile
# approximation: for k failures in n trials, U_α = 1 - BetaInv(α; n-k, k+1)
# which equals the Clopper–Pearson upper bound for binomial p.


def binomial_one_sided_upper_bound(
    failures: int,
    n: int,
    *,
    confidence: float = 0.95,
) -> float | None:
    """One-sided upper confidence bound for a binomial proportion.

    Method: Clopper–Pearson (exact) upper bound via the Beta–F relationship:
        U = BetaQuantile(confidence; failures + 1, n - failures)
    When failures=0 this reduces to ``1 - (1-confidence)^(1/n)``
    (rule of three generalized).

    Returns None when n == 0. Never reports 0.0 solely because failures == 0
    and n > 0 — the bound is strictly > 0 for finite n.
    """
    if n <= 0:
        return None
    if failures < 0 or failures > n:
        raise ValueError("failures must be in [0, n]")
    alpha = 1.0 - confidence
    if failures == 0:
        # Exact Clopper–Pearson upper bound with 0 failures.
        return 1.0 - (alpha ** (1.0 / n))
    # For failures > 0 use incomplete-beta inversion via continued fraction /
    # binary search on the regularized incomplete beta.
    return _beta_quantile(confidence, failures + 1, n - failures)


def _beta_cdf(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a,b) via continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Use continued fraction for Ix; symmetry when x > (a+1)/(a+b+2).
    ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

    def _betacf(aa: float, bb: float, xx: float) -> float:
        max_iter = 200
        eps = 3e-12
        am, bm = 1.0, 1.0
        az = 1.0
        qab = aa + bb
        qap = aa + 1.0
        qam = aa - 1.0
        bz = 1.0 - qab * xx / qap
        for m in range(1, max_iter + 1):
            em = float(m)
            tem = em + em
            d = em * (bb - em) * xx / ((qam + tem) * (aa + tem))
            ap = az + d * am
            bp = bz + d * bm
            d = -(aa + em) * (qab + em) * xx / ((aa + tem) * (qap + tem))
            app = ap + d * az
            bpp = bp + d * bz
            am, bm, az, bz = ap / bpp, bp / bpp, app / bpp, 1.0
            if abs(app - ap) < eps * abs(app):
                return app
        return az

    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - ln_beta) / a
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    return 1.0 - (
        math.exp(b * math.log(1.0 - x) + a * math.log(x) - ln_beta) / b
    ) * _betacf(b, a, 1.0 - x)


def _beta_quantile(p: float, a: float, b: float) -> float:
    """Inverse of regularized incomplete beta via bisection."""
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _beta_cdf(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass
class SettlementTransferAssessment:
    """Formal NWS→CLINYC settlement transfer assessment object."""

    source_regime: str
    target_regime: str

    observed_pair_n: int

    mean_difference_f: float | None
    median_difference_f: float | None
    mae_f: float | None
    rmse_f: float | None
    std_f: float | None

    exact_integer_match_rate: float | None
    within_1f_rate: float | None
    same_bucket_rate: float | None
    winner_change_rate: float | None

    historical_status: str  # development evidence summary label

    prospective_n: int
    prospective_mismatches: int

    transfer_method: str  # identity

    transfer_validated: bool

    warnings: list[str] = field(default_factory=list)

    # Extended diagnostic fields (serialized with the assessment).
    transfer_evidence_class: str = EVIDENCE_CLASS_DEVELOPMENT
    exact_integer_mismatches: int = 0
    same_bucket_mismatches: int = 0
    integer_mismatch_rate_observed: str | None = None
    bucket_mismatch_rate_observed: str | None = None
    integer_mismatch_95_upper: float | None = None
    bucket_mismatch_95_upper: float | None = None
    confidence_method: str = (
        "Clopper-Pearson one-sided 95% upper bound on binomial mismatch rate"
    )
    transfer_status: str = TRANSFER_STATUS_EXPERIMENTAL
    development_n: int = 0
    prospective_exact_mismatches: int = 0
    prospective_bucket_mismatches: int = 0
    prospective_mean_difference_f: float | None = None
    prospective_target_n: int = 20
    hypothesis_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_hypothesis_payload(*, registered_at: str | None = None) -> dict[str, Any]:
    registered = registered_at or datetime.now(timezone.utc).isoformat()
    return {
        "hypothesis_id": HYPOTHESIS_ID_CLINYC_TRANSFER_V1,
        "registered_at": registered,
        "source_regime": REGIME_NWS_CLI_KNYC,
        "target_regime": REGIME_WEATHER_COMPANY_CLINYC,
        "transfer_rule": "identity",
        "development_pair_count": 42,
        "validation_start_date": None,  # filled: first TWC event after registration
        "criteria": {
            "min_new_paired_settled_events": 20,
            "report_fields": [
                "exact_mismatch_count",
                "same_bucket_mismatch_count",
                "mean_difference",
                "directional_bias",
            ],
            "decision_rule": (
                "Do NOT auto-validate from sample count alone. "
                "transfer_validated becomes true only when "
                "prospective_n >= min_new_paired_settled_events AND "
                "prospective exact-integer mismatch rate and same-bucket "
                "mismatch rate are each <= development rates + 0 (strict: "
                "zero exact and zero bucket mismatches in the prospective "
                "window) AND mean |difference| <= 0.25F AND no material "
                "directional bias (|mean_difference| <= 0.25F). "
                "Human-auditable: decision is computed by "
                "evaluate_prospective_transfer_validation() only — "
                "never by a manual UI/API toggle."
            ),
            "max_prospective_exact_mismatches": 0,
            "max_prospective_bucket_mismatches": 0,
            "max_abs_mean_difference_f": 0.25,
        },
        "notes": [
            "Development pairs (pre-registration) motivate identity transfer; "
            "they are NOT confirmation evidence.",
            "Do not silently modify criteria after registration.",
        ],
    }


def load_or_register_hypothesis(
    *,
    force_reregister: bool = False,
    registered_at: str | None = None,
) -> dict[str, Any]:
    """Load frozen hypothesis; register once if missing.

    Criteria are never silently rewritten after the file exists.
    """
    HYPOTHESES_DIR.mkdir(parents=True, exist_ok=True)
    if HYPOTHESIS_PATH.exists() and not force_reregister:
        payload = json.loads(HYPOTHESIS_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    payload = default_hypothesis_payload(registered_at=registered_at)
    HYPOTHESIS_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def assign_evidence_class(
    target_date: str,
    *,
    hypothesis: dict[str, Any] | None = None,
) -> str:
    """Classify a pair as development or prospective from frozen registration."""
    hyp = hypothesis if hypothesis is not None else load_or_register_hypothesis()
    return evidence_class_for_date(
        target_date,
        registered_at=str(hyp.get("registered_at") or ""),
        validation_start_date=(
            str(hyp["validation_start_date"])
            if hyp.get("validation_start_date")
            else None
        ),
    )


def evidence_class_for_date(
    target_date: str,
    *,
    registered_at: str,
    validation_start_date: str | None,
) -> str:
    """Pure classifier (no file I/O) for tests and rebuilds."""
    start = validation_start_date
    if start:
        return (
            EVIDENCE_CLASS_PROSPECTIVE
            if str(target_date) >= str(start)
            else EVIDENCE_CLASS_DEVELOPMENT
        )
    reg_day = str(registered_at)[:10]
    if str(target_date) > reg_day:
        return EVIDENCE_CLASS_PROSPECTIVE
    return EVIDENCE_CLASS_DEVELOPMENT


def set_validation_start_date_if_needed(
    hypothesis: dict[str, Any],
    *,
    twc_dates: Sequence[str],
) -> dict[str, Any]:
    """Freeze validation_start_date to first TWC event after registration."""
    if hypothesis.get("validation_start_date"):
        return hypothesis
    reg_day = str(hypothesis.get("registered_at") or "")[:10]
    future = sorted(d for d in twc_dates if d and str(d) > reg_day)
    if future:
        hypothesis = dict(hypothesis)
        hypothesis["validation_start_date"] = future[0]
        HYPOTHESIS_PATH.write_text(
            json.dumps(hypothesis, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        # No future TWC dates yet — leave None; all current pairs = development.
        pass
    return hypothesis


def evaluate_prospective_transfer_validation(
    *,
    prospective_n: int,
    prospective_exact_mismatches: int,
    prospective_bucket_mismatches: int,
    prospective_mean_difference_f: float | None,
    hypothesis: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Return (validated, reasons). Never accepts a manual override flag."""
    hyp = hypothesis if hypothesis is not None else load_or_register_hypothesis()
    criteria = hyp.get("criteria") or {}
    reasons: list[str] = []
    min_n = int(criteria.get("min_new_paired_settled_events") or 20)
    max_exact = int(criteria.get("max_prospective_exact_mismatches") or 0)
    max_bucket = int(criteria.get("max_prospective_bucket_mismatches") or 0)
    max_abs_mean = float(criteria.get("max_abs_mean_difference_f") or 0.25)

    if prospective_n < min_n:
        reasons.append(
            f"prospective_n={prospective_n} < min_new_paired_settled_events={min_n}"
        )
    if prospective_exact_mismatches > max_exact:
        reasons.append(
            f"prospective exact mismatches={prospective_exact_mismatches} "
            f"> max={max_exact}"
        )
    if prospective_bucket_mismatches > max_bucket:
        reasons.append(
            f"prospective bucket mismatches={prospective_bucket_mismatches} "
            f"> max={max_bucket}"
        )
    if prospective_mean_difference_f is None and prospective_n > 0:
        reasons.append("prospective mean difference unavailable")
    elif prospective_mean_difference_f is not None:
        if abs(prospective_mean_difference_f) > max_abs_mean:
            reasons.append(
                f"|mean_difference|={abs(prospective_mean_difference_f):.4f} "
                f"> {max_abs_mean}"
            )

    validated = len(reasons) == 0 and prospective_n >= min_n
    if validated:
        reasons.append("all frozen prospective criteria satisfied")
    return validated, reasons


def assess_direct_clinyc_eligibility(
    calibration_rows: Sequence[dict[str, Any]],
    *,
    before_date: str | None = None,
    paired_clinyc_event_n: int | None = None,
) -> dict[str, Any]:
    """Honest eligibility: dataset existence ≠ operational usability.

    Residuals are same-run only. Hierarchical thresholds still apply:
      MIN_N_MONTH=30, MIN_N_SEASON=50, MIN_N_RUN=80, MIN_RUN_HISTORY=20.

    ``direct_clinyc_dataset_exists`` is True when paired CLINYC settlement
    outcomes exist (``paired_clinyc_event_n``) OR residual rows exist —
    not when operational pools clear thresholds.
    """
    clinyc = [
        r
        for r in calibration_rows
        if (r.get("settlement_source_regime") or r.get("target_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
        and r.get("residual_f") not in (None, "")
    ]
    if before_date:
        clinyc = [r for r in clinyc if str(r.get("target_date") or "") < before_date]

    # Unique events / dates for residual-based dataset signal.
    event_dates = sorted({str(r.get("target_date")) for r in clinyc if r.get("target_date")})
    residual_dataset = len(event_dates) > 0
    pair_dataset = bool(paired_clinyc_event_n and paired_clinyc_event_n > 0)
    dataset_exists = residual_dataset or pair_dataset

    by_run: dict[str, dict[str, Any]] = {}
    any_run_eligible = False
    for run in STANDARDIZED_RUNS:
        run_id = str(run["run_id"])
        run_rows = [r for r in clinyc if r.get("run_id") == run_id]
        # Count unique dates for history; residual rows for pool sizes.
        unique_dates = sorted({str(r.get("target_date")) for r in run_rows})
        n_history = len(unique_dates)
        # Month / season / run pool sizes = residual row counts (one per date typically).
        # For eligibility at a generic "current" point, use full same-run history.
        month_counts: dict[int, int] = {}
        season_counts: dict[str, int] = {}
        for r in run_rows:
            try:
                month = int(r.get("month") or str(r.get("target_date") or "0000-00")[5:7])
            except (TypeError, ValueError):
                continue
            month_counts[month] = month_counts.get(month, 0) + 1
            season = str(r.get("season") or "")
            if season:
                season_counts[season] = season_counts.get(season, 0) + 1

        max_month_n = max(month_counts.values()) if month_counts else 0
        max_season_n = max(season_counts.values()) if season_counts else 0
        run_n = len(run_rows)

        month_ok = max_month_n >= MIN_N_MONTH
        season_ok = max_season_n >= MIN_N_SEASON
        run_ok = run_n >= MIN_N_RUN
        history_ok = n_history >= MIN_RUN_HISTORY
        # Operationally eligible only if some hierarchical pool can fire.
        eligible = history_ok and (month_ok or season_ok or run_ok)
        any_run_eligible = any_run_eligible or eligible

        by_run[run_id] = {
            "unique_dates": n_history,
            "residual_rows": run_n,
            "max_month_n": max_month_n,
            "max_season_n": max_season_n,
            "month_eligible": month_ok,
            "season_eligible": season_ok,
            "run_global_eligible": run_ok,
            "burn_in_ok": history_ok,
            "operationally_eligible": eligible,
            "thresholds": {
                "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
                "MIN_N_MONTH": MIN_N_MONTH,
                "MIN_N_SEASON": MIN_N_SEASON,
                "MIN_N_RUN": MIN_N_RUN,
            },
        }

    prediction_status = (
        "OK"
        if any_run_eligible
        else "INSUFFICIENT_TARGET_REGIME_HISTORY"
    )
    return {
        "direct_clinyc_dataset_exists": dataset_exists,
        "direct_clinyc_operationally_eligible": any_run_eligible,
        "unique_event_dates": len(event_dates),
        "paired_clinyc_event_n": paired_clinyc_event_n,
        "residual_rows": len(clinyc),
        "prediction_status": prediction_status,
        "by_run": by_run,
        "note": (
            "Dataset existence (paired CLINYC outcomes and/or residual rows) "
            "is distinct from operational eligibility under frozen MIN_N_* "
            "thresholds. N>=MIN_RUN_HISTORY alone is not sufficient."
        ),
    }


def build_transfer_assessment(
    pairs: Sequence[dict[str, Any]],
    *,
    hypothesis: dict[str, Any] | None = None,
) -> SettlementTransferAssessment:
    """Build assessment from TWC-regime paired rows (development + prospective)."""
    hyp = hypothesis if hypothesis is not None else load_or_register_hypothesis()
    twc = [
        p
        for p in pairs
        if (p.get("regime") or p.get("settlement_source_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    ]

    def _is_true(val: Any) -> bool:
        return str(val).lower() in {"true", "1", "yes"}

    def _class(p: dict[str, Any]) -> str:
        return str(p.get("evidence_class") or EVIDENCE_CLASS_DEVELOPMENT)

    development = [p for p in twc if _class(p) == EVIDENCE_CLASS_DEVELOPMENT]
    prospective = [p for p in twc if _class(p) == EVIDENCE_CLASS_PROSPECTIVE]

    def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "n": 0,
                "diffs": [],
                "exact_mismatches": 0,
                "bucket_mismatches": 0,
                "mean": None,
                "median": None,
                "mae": None,
                "rmse": None,
                "std": None,
                "exact_rate": None,
                "within_1": None,
                "same_bucket_rate": None,
                "winner_change_rate": None,
            }
        diffs = [float(p["difference_f"]) for p in rows]
        abs_d = [abs(d) for d in diffs]
        exact_m = sum(
            1
            for p in rows
            if not _is_true(p.get("exact_integer_match", p.get("same_integer")))
        )
        bucket_m = sum(
            1
            for p in rows
            if not _is_true(p.get("same_kalshi_bucket"))
        )
        n = len(rows)
        return {
            "n": n,
            "diffs": diffs,
            "exact_mismatches": exact_m,
            "bucket_mismatches": bucket_m,
            "mean": statistics.fmean(diffs),
            "median": statistics.median(diffs),
            "mae": statistics.fmean(abs_d),
            "rmse": math.sqrt(statistics.fmean(d * d for d in diffs)),
            "std": statistics.pstdev(diffs) if n > 1 else 0.0,
            "exact_rate": (n - exact_m) / n,
            "within_1": sum(1 for d in abs_d if d <= 1.0 + 1e-9) / n,
            "same_bucket_rate": (n - bucket_m) / n,
            "winner_change_rate": bucket_m / n,
        }

    # Historical / development metrics (primary descriptive evidence).
    hist_rows = development if development else twc
    m = _metrics(hist_rows)
    p = _metrics(prospective)

    int_upper = binomial_one_sided_upper_bound(m["exact_mismatches"], m["n"])
    bucket_upper = binomial_one_sided_upper_bound(m["bucket_mismatches"], m["n"])

    validated, val_reasons = evaluate_prospective_transfer_validation(
        prospective_n=p["n"],
        prospective_exact_mismatches=p["exact_mismatches"],
        prospective_bucket_mismatches=p["bucket_mismatches"],
        prospective_mean_difference_f=p["mean"],
        hypothesis=hyp,
    )

    warnings = [
        "Development pairs motivate identity transfer; they do not certify validation.",
        (
            f"Observed integer mismatches = {m['exact_mismatches']}/{m['n']}; "
            "do not interpret as 0% future mismatch risk."
        ),
    ]
    if not validated:
        warnings.extend(val_reasons)

    return SettlementTransferAssessment(
        source_regime=REGIME_NWS_CLI_KNYC,
        target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        observed_pair_n=m["n"],
        mean_difference_f=m["mean"],
        median_difference_f=m["median"],
        mae_f=m["mae"],
        rmse_f=m["rmse"],
        std_f=m["std"],
        exact_integer_match_rate=m["exact_rate"],
        within_1f_rate=m["within_1"],
        same_bucket_rate=m["same_bucket_rate"],
        winner_change_rate=m["winner_change_rate"],
        historical_status="development_evidence_only",
        prospective_n=p["n"],
        prospective_mismatches=p["exact_mismatches"],
        transfer_method="identity",
        transfer_validated=validated,
        warnings=warnings,
        transfer_evidence_class=EVIDENCE_CLASS_DEVELOPMENT,
        exact_integer_mismatches=m["exact_mismatches"],
        same_bucket_mismatches=m["bucket_mismatches"],
        integer_mismatch_rate_observed=f"{m['exact_mismatches']} / {m['n']}",
        bucket_mismatch_rate_observed=f"{m['bucket_mismatches']} / {m['n']}",
        integer_mismatch_95_upper=int_upper,
        bucket_mismatch_95_upper=bucket_upper,
        transfer_status=(
            TRANSFER_STATUS_VALIDATED if validated else TRANSFER_STATUS_EXPERIMENTAL
        ),
        development_n=m["n"],
        prospective_exact_mismatches=p["exact_mismatches"],
        prospective_bucket_mismatches=p["bucket_mismatches"],
        prospective_mean_difference_f=p["mean"],
        prospective_target_n=int(
            (hyp.get("criteria") or {}).get("min_new_paired_settled_events") or 20
        ),
        hypothesis_id=str(hyp.get("hypothesis_id") or HYPOTHESIS_ID_CLINYC_TRANSFER_V1),
    )


def write_transfer_assessment(assessment: SettlementTransferAssessment) -> Path:
    cache.ensure_dirs()
    cache.write_json(TRANSFER_REPORT_PATH, assessment.to_dict())
    return TRANSFER_REPORT_PATH


def load_transfer_assessment() -> dict[str, Any] | None:
    raw = cache.read_json(TRANSFER_REPORT_PATH, default=None)
    return raw if isinstance(raw, dict) else None
