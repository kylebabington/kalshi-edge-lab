"""Frozen Phase 2 rules. Every value here is copied into the protocol before any 2026 scoring.

Changing anything in this file changes the Phase 2 code hash; a frozen protocol then refuses to
evaluate (bump the protocol version instead).
"""

from __future__ import annotations

from ..core import REPO_ROOT

PROTOCOL = "sports_phase2_v1"
PHASE1_PROTOCOL = "sports_phase1_v2"
PHASE1_CODE_HASH = "5672164410b0a02702382137a19b020c791ef55e171ed6dca4524dc53c04a195"

PHASE2_DATA = REPO_ROOT / "data" / "sports" / "phase2"
OOF_DIR = PHASE2_DATA / "oof"
MARKET_DIR = PHASE2_DATA / "market"
JOURNALS = PHASE2_DATA / "predictions"
CAPTURE_DIR = PHASE2_DATA / "prospective"
CAPTURE_CACHE = REPO_ROOT / "data" / "cache" / "sports" / "raw_capture"
RESULTS2 = REPO_ROOT / "data" / "results" / "sports_phase2"

# ---------------------------------------------------------------- periods (UTC, [start, end))
PERIODS = {
    "F1": ("2025-01-01T00:00:00Z", "2025-07-01T00:00:00Z"),
    "F2": ("2025-07-01T00:00:00Z", "2025-10-01T00:00:00Z"),
    "F3": ("2025-10-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "EVAL": ("2026-01-01T00:00:00Z", "2026-10-06T00:00:00Z"),
}
FOLDS = ["F1", "F2", "F3"]
ROLE = {"F1": "train", "F2": "train", "F3": "validation", "EVAL": "evaluation"}
EVAL_NOTE = ("2026 (EVAL) was already examined in Phase 1 (v1, v2). Phase 2 scores on it are retrospective "
             "development results, not confirmation; no candidate is selected on 2026.")

# Extra delay (hours) added to comp.result_lag_hours when placing a fold's parameter-fitting
# boundary. The boundary is then *verified*: every training label used by selection must be
# available (per the Phase 1 availability rules) at or before fold start - horizon.
BOUNDARY_EXTRA_LAG_H = {"espn_team": 0, "espn_fight": 0, "espn_golf": 5 * 24, "jolpica": 5 * 24, "kalshi_only": 7 * 24}

# ---------------------------------------------------------------- candidates
CANDIDATES = {
    "frozen": "Phase 1 primary model (elo for two-way game winners, score model otherwise), parameters re-selected "
              "per fold for out-of-fold rows; for 2026 the sports_phase1_v2 journal forecasts verbatim",
    "market": "Kalshi mid at the forecast cutoff (last hourly candle ended by cutoff, age <= 3h, two-sided book)",
    "calibrated": "logistic recalibration of the frozen model (form and ridge strength selected on F3)",
    "blend": "sigma(c + w*logit(p_model) + (1-w)*logit(p_market)) (w, c selected on F3)",
}
CAL_GROUP = "competition x contract family (fit separately per group; never pooled across groups)"
CLIP = 1e-4
CAL = {
    "x": "logit(clip(p, 1e-4, 1 - 1e-4))",
    "forms": {"identity": "p unchanged", "intercept": "sigma(a + x)", "platt": "sigma(a + b*x)"},
    "lambda_grid": [0.0, 0.1, 1.0, 10.0],
    "penalty": "lambda * (a^2 + (b-1)^2)  (identity is the zero-penalty point)",
    "objective": "sum_i w_i * logloss_i + penalty, w_i = 1 / (contracts of the event in the group); "
                 "event-equal (each event weighs 1)",
    "optimizer": {"method": "Newton-Raphson with step halving", "max_iter": 100, "tol_step": 1e-10,
                  "tol_grad": 1e-9, "max_halvings": 30, "start": "identity (a=0, b=1)",
                  "non_convergence": "candidate form unavailable for that lambda"},
    "selection": "minimum F3 event-equal validation log loss; ties within 1e-12 broken by form order "
                 "identity < intercept < platt, then larger lambda",
    "refit": "selected form and lambda refit on F1+F2+F3 out-of-fold rows, then frozen for 2026",
    "min_train": {"events": 200, "per_class_events": 20},
    "min_validation": {"events": 100, "per_class_events": 10},
    "class_count": "events with at least one y=1 contract / at least one y=0 contract",
}
BLEND = {
    "w_grid": [round(i / 10, 1) for i in range(11)],
    "intercept": {"none": "c = 0", "ridge_lambda_grid": [0.0, 1.0, 10.0], "penalty": "lambda * c^2"},
    "optimizer": "same Newton settings as calibration (1-D in c)",
    "inputs": "only contracts with BOTH a frozen-model forecast and a valid market quote at the cutoff; the "
              "training/validation market inputs come from the frozen historical sample",
    "selection": "minimum F3 event-equal validation log loss over matched rows; ties within 1e-12 broken by "
                 "intercept none < intercept, larger lambda, then smaller w",
    "refit": "intercept refit at the selected (w, lambda) on F1+F2+F3 matched rows",
    "min_train": {"events": 150, "per_class_events": 15},
    "min_validation": {"events": 75, "per_class_events": 8},
}
COMPARISONS = [
    ("calibrated", "frozen", "all scored 2026 contracts of the group"),
    ("blend", "market", "2026 contracts in the market sample with a valid quote"),
    ("frozen", "market", "2026 contracts in the market sample with a valid quote"),
    ("blend", "frozen", "2026 contracts in the market sample with a valid quote"),
]
MIN_MATCHED = {"default": 50, "field_finish": 15}
SUFFICIENCY_RULE = ("applied to the final matched sample of each comparison (unique event clusters with every "
                    "required input after all filters); below the minimum: descriptive point values only")
BOOTSTRAP2 = {"B": 2000, "seed": 20261009, "unit": "event cluster", "ci": [0.025, 0.975],
              "weighting": ["contract_weighted", "event_equal"]}

# ---------------------------------------------------------------- historical market sample
MARKET_FAMILIES = ["game_winner", "spread", "total", "team_total"]
NO_NEW_QUOTES = {
    "segment": "segment markets (quarters/halves/innings): no new historical quotes by scope decision",
    "score_props": "score props: no new historical quotes by scope decision",
    "field_finish": "field finish (golf/F1): no new historical quotes by scope decision",
    "player_props": "player/appearance markets are not modelled (Phase 1 exclusion)",
}
SAMPLE_SEED = "sports_phase2_v1"
EVENT_CAPS = {
    "independent": {"train": 300, "validation": 200, "evaluation": 300},
    "kalshi_only": {"train": 150, "validation": 100, "evaluation": 150},
}
SAMPLE_PERIOD = {"train": ("F1", "F2"), "validation": ("F3",), "evaluation": ("EVAL",)}
CONTRACT_RULES = {
    "game_winner": "1 per event: home side (non-tie) if present, else lexicographically first ticker (Phase 1 rule)",
    "spread": "2 per event: per side (home, away), the lower-median strike among that side's eligible strikes",
    "total": "2 per event: the lower-median and upper-median strike among eligible strikes (1 if only one)",
    "team_total": "2 per event: per side, the lower-median strike among that side's eligible strikes",
}
ELIGIBILITY = ("contract status ok, family in scope, a frozen-model forecast exists (label-free), series not in "
               "the failed reconciliation cohort, and listing time (max(created_time, open_time) from the cached raw "
               "market pages) <= forecast cutoff; contracts with unknown listing time are never sampled")
TIERS = [
    ("1_gw_train_validation", "game_winner", ("train", "validation")),
    ("2_spread_total", ("spread", "total"), ("train", "validation", "evaluation")),
    ("3_team_total", "team_total", ("train", "validation", "evaluation")),
    ("4_gw_evaluation", "game_winner", ("evaluation",)),
]
BUDGET = {"max_unique_requests": 60000, "max_retries_total": 5000, "per_request_max_retries": 4,
          "min_interval_s": 0.5, "truncation": "if uncached requests exceed max_unique_requests, whole tiers are "
          "kept in tier order and the first tier that does not fit is truncated in frozen event order; later "
          "tiers are dropped. Failed quotes are never replaced and the sample is never expanded."}
QUOTE = {"lookback_hours": 24, "max_age_hours": 3, "period_interval_min": 60,
         "rule": "same as Phase 1: latest hourly candle with end_period_ts <= cutoff, two-sided yes book, "
                 "age <= 3h; bid <= 0 or ask >= 1 is no quote"}

# ---------------------------------------------------------------- prospective capture
CAPTURE = {
    "modes": {
        "early": "EARLY_SNAPSHOT: development/smoke record for events whose T-60 cutoff is still in the future; "
                 "records its actual horizon; never scored as a checkpoint forecast",
        "checkpoint": "CHECKPOINT: only inside the frozen window; evidence must be retrieved at or before the "
                      "evidence cutoff; otherwise MISSED (never backfilled)",
    },
    "scheduled_cutoff": "event start - 60 minutes",
    "window_minutes_before_cutoff": 20,
    "evidence_cutoff": "equal to the scheduled cutoff",
    "scope": "espn_team game_winner with an Elo forecast (two-way, full game)",
    "timing_fields": ["scheduled_cutoff", "evidence_cutoff", "capture_started_at", "finalized_at"],
    "evidence": "fresh per-run raw cache under data/cache/sports/raw_capture/<run_id>/ (Kalshi market list + "
                "ESPN scoreboard for the capture date); replayed offline by capture-replay",
    "write_once": "one JSON record per (mode, event, run); existing records are never rewritten",
}
