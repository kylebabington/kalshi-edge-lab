# Phase 6 v2_1 research summary — KNYC as-of observations + operational replay

**Status: RESEARCH_ONLY / NO_BET.** Incumbent: calibrated GFS. `SHADOW_GFS_HRRR_EQUAL_V1` (0.5·P_gfs + 0.5·P_hrrr) is **SHADOW ONLY** — not promoted. No prices, trades, sizing or learned weights. A confidence interval excluding zero would not be validation.

Version `v2_1` supersedes `v2` (change type `diagnostic_reason_only`): 53 HRRR d0_0900 version-C rows use 09Z side-cycle runs whose 18-hour horizon ends at 03Z (23:00 EDT); the Single Runs payload pads later timestamps with nulls, so the final climate-day hour (04Z) is present but null. v2 labeled these missing_or_null_hours_in_remaining_window; v2_1 labels them selected_run_horizon_short_null_padded. Eligibility, values and scores are unchanged.

## Methodology

- **Climate day (shared):** observation and model windows both use the NWS CLI climate day (midnight EST to midnight EST all year); during EDT this includes 00:00 EDT of the next calendar date and excludes 00:00 EDT of the target date.
- **Observation window:** NWS CLI climate day (00:00 Local Standard Time, UTC-5, all year) of the target date through the checkpoint; during EDT 00:00-00:59 EDT reports belong to the previous climate day; newest usable report ≤ 120 min old, no gap > 180 min.
- **Availability (frozen):** available_at = observation_time + 20 min (later supplied availability wins).
- **Version C model window:** max hourly forecast at valid times in [checkpoint, next midnight EST after the target climate date) from the same selected run; start and end come from climate_day_bounds_utc, end exclusive; every hour must be present with a value or the row is REPLAY_UNAVAILABLE (never a partial max, never a Phase 5 fallback).
- **Incomplete-window reasons (all REPLAY_UNAVAILABLE):** contiguous null padded tail after last real value → `selected_run_horizon_short_null_padded`; final expected timestamp beyond end of series → `run_horizon_ends_before_end_of_target_date`; missing or null hour followed by later real value → `missing_or_null_hours_in_remaining_window`.
- **Run selection:** inherited unchanged from Phase 5 (GFS latency 6h, HRRR latency 3h, newest available run).
- **Calibration:** same model + same checkpoint + same regime + target_date < T, within one version; versions recalibrated separately.
- **Cohorts:** (target_date, checkpoint) where GFS and HRRR version-C rows are both FULL_OPERATIONAL_REPLAY; eligible pairs scored OK for GFS, HRRR and shadow in A, B and C.
- **Bootstrap:** target_date cluster (all checkpoints of a date resampled together), n=1000, seed=42, 95% percentile.

## Observation coverage

IEM ASOS KNYC, 114 target dates: 3642 reports, 3642 valid, rejections none.

| Checkpoint | Status | Newest usable age min (min/median/max) | Delayed reports excluded | Obs binds (C) |
|---|---|---|---|---|
| d0_0600 | {'OK': 114} | 20/69/69 | 116 | 0.035 (n=228) |
| d0_0900 | {'OK': 114} | 20/69/69 | 125 | 0.034 (n=175) |
| d0_1200 | {'OK': 114} | 23/69/69 | 117 | 0.061 (n=228) |
| d0_1500 | {'OK': 114} | 23/69/69 | 120 | 0.193 (n=228) |

| Checkpoint | Eligible pairs (both models FULL in C) | Common cohort |
|---|---|---|
| dminus1_1800 | 113 | 42 |
| d0_0600 | 114 | 44 |
| d0_0900 | 61 | 4 |
| d0_1200 | 114 | 44 |
| d0_1500 | 114 | 44 |

Version C model-window unavailability: HRRR d0_0900: {'selected_run_horizon_short_null_padded': 53}.

## Results (common cohort: 178 pairs, 44 dates)

Multiclass Brier (lower is better); shadow − GFS is the paired mean with date-clustered 95% CI.

**A_phase5_model_only**

| Scope | n | GFS | HRRR | Shadow | Shadow − GFS [95% CI] |
|---|---|---|---|---|---|
| dminus1_1800 | 42 | 0.7072 | 0.7703 | 0.7156 | 0.0085 [-0.0318, 0.0470] |
| d0_0600 | 44 | 0.7624 | 0.7661 | 0.7261 | -0.0364 [-0.0952, 0.0207] |
| d0_0900 | 4 | 0.9267 | 0.8735 | 0.8351 | -0.0916 [-0.4942, 0.2007] |
| d0_1200 | 44 | 0.7949 | 0.7557 | 0.7335 | -0.0613 [-0.1310, 0.0061] |
| d0_1500 | 44 | 0.7949 | 0.7655 | 0.7337 | -0.0612 [-0.1358, 0.0065] |
| pooled_intraday | 136 | 0.7882 | 0.7657 | 0.7341 | -0.0541 [-0.1271, 0.0080] |
| pooled_all | 178 | 0.7691 | 0.7668 | 0.7298 | -0.0393 [-0.0976, 0.0127] |

**B_phase5_model_max_plus_obs**

| Scope | n | GFS | HRRR | Shadow | Shadow − GFS [95% CI] |
|---|---|---|---|---|---|
| dminus1_1800 | 42 | 0.7072 | 0.7703 | 0.7156 | 0.0085 [-0.0318, 0.0470] |
| d0_0600 | 44 | 0.7582 | 0.7657 | 0.7235 | -0.0347 [-0.0912, 0.0215] |
| d0_0900 | 4 | 0.9169 | 0.8735 | 0.8301 | -0.0868 [-0.4795, 0.2002] |
| d0_1200 | 44 | 0.7683 | 0.7493 | 0.7191 | -0.0492 [-0.1060, 0.0114] |
| d0_1500 | 44 | 0.7742 | 0.7587 | 0.7213 | -0.0528 [-0.1122, 0.0029] |
| pooled_intraday | 136 | 0.7713 | 0.7613 | 0.7245 | -0.0468 [-0.1094, 0.0123] |
| pooled_all | 178 | 0.7562 | 0.7634 | 0.7224 | -0.0337 [-0.0849, 0.0161] |

**C_remaining_day_max_plus_obs**

| Scope | n | GFS | HRRR | Shadow | Shadow − GFS [95% CI] |
|---|---|---|---|---|---|
| dminus1_1800 | 42 | 0.7347 | 0.7664 | 0.7266 | -0.0081 [-0.0535, 0.0360] |
| d0_0600 | 44 | 0.7631 | 0.7588 | 0.7231 | -0.0400 [-0.0976, 0.0168] |
| d0_0900 | 4 | 0.9169 | 0.8735 | 0.8301 | -0.0868 [-0.4795, 0.2002] |
| d0_1200 | 44 | 0.7584 | 0.7465 | 0.7108 | -0.0476 [-0.1060, 0.0117] |
| d0_1500 | 44 | 0.7620 | 0.7609 | 0.7046 | -0.0574 [-0.1137, -0.0020] |
| pooled_intraday | 136 | 0.7657 | 0.7589 | 0.7163 | -0.0495 [-0.1089, 0.0084] |
| pooled_all | 178 | 0.7584 | 0.7606 | 0.7187 | -0.0397 [-0.0891, 0.0085] |

`d0_0900` has only 4 common-cohort pairs and is not interpretable on its own.

## v1 → v2 (climate-day model-window fix)

- Exact expected-hour sets compared per row (1138 rows; v1 reconstruction mismatches: 0).
- Rows gaining the final 00:00 EDT hour: 1138; rows also losing the target-date 00:00 EDT leading hour (dminus1_1800, same 24-hour count): 226.
- Model maxima changed: 8 — GFS dminus1_1800: 2026-04-25 -0.7°F, 2026-05-21 -0.7°F, 2026-05-23 -0.6°F, 2026-05-30 -4.3°F; HRRR dminus1_1800: 2026-04-25 -0.2°F, 2026-05-21 -2.1°F, 2026-05-30 -2.1°F, 2026-07-06 -1.3°F.
- Newly unavailable: 53 (all HRRR d0_0900; see diagnosis below).
- Common cohort 218 → 178 pairs (d0_0900 44 → 4).

| Version C pooled_intraday | v1 | v2 |
|---|---|---|
| n | 176 | 136 |
| GFS Brier | 0.7630 | 0.7657 |
| HRRR Brier | 0.7602 | 0.7589 |
| SHADOW Brier | 0.7163 | 0.7163 |
| Shadow − GFS [95% CI] | -0.0468 [-0.1007, 0.0080] | -0.0495 [-0.1089, 0.0084] |

Conclusion changed: **False** (shadow point estimate better than GFS in both; interval includes zero in both).

## v2 → v2_1 (diagnostic reason relabel)

- Verification passed: **True** (failures: none).
- Relabeled rows: HRRR C 53, HRRR B (window-diagnostic column only) 53, GFS C/B 0/0; unexpected cell changes: 0.
- Coverage reason counts: {'missing_or_null_hours_in_remaining_window': 53} → {'selected_run_horizon_short_null_padded': 53} (totals preserved: True).
- Scores, CIs, paired rows, eligibility and cohort membership identical: True. Per-bucket probabilities are not persisted in Phase 6 artifacts. Every scoring input (forecast_high_f, residual_f, replay_mode, eligibility, cohort keys) is cell-identical and every persisted per-pair Brier / log loss and bootstrap CI is identical, so the probabilities are unchanged.
- Methodology differs from v2 only by the planned additions: True. v1/v2 files byte-for-byte unchanged (24 hashed, changed: none).

## 09:00 coverage diagnosis (HRRR d0_0900)

- 53 unavailable rows; selected init hours {'09Z': 53}, real-value horizon {'18.0': 53} h. The selected 09Z run's last real value is 03Z (23:00 EDT); the CLI climate day needs 04Z (00:00 EDT). The payload pads later timestamps with nulls.
- Newer latency-eligible inits (10Z) per row: cache status totals {'absent': 53} — not represented in the local Phase 5 cache; rows with a newer usable run: 0.
- Older eligible runs with a usable high existed for 53 rows (53 with an hourly-cached run covering the window, e.g. 06Z extended cycle). Phase 5's `latest_available_by_latency` policy prefers the newest available run, so they were not selected. Nothing was substituted.
- Local Phase 5 HRRR cache init-hour profile: {'00Z': 168, '03Z': 53, '06Z': 220, '09Z': 54, '12Z': 219, '15Z': 107, '18Z': 167, '21Z': 54}; values {'usable': 1042}.
- Source restriction: The three-hourly pattern in the local Phase 5 cache is not a restriction in this repository's code. Hourly inits are not represented in the local Phase 5 cache; whether the source lacks them or the requests returned nothing usable is not established by local logs or code. (get_hrrr_run_high writes a cache entry only when it obtains a non-null high; failed requests, non-OK responses and runs without target-date values are not written, so the cache cannot distinguish 'requested and unusable' from 'never requested'.)
- Per-row table: [`phase6_v2_1_hrrr_d0_0900_coverage_audit.csv`](phase6_v2_1_hrrr_d0_0900_coverage_audit.csv).

## Artifacts

Tracked: `data/weather/calibration/*_v1.csv`, `*_v2.csv`, `*_v2_1.csv`; this summary; the audit CSV. Local only (gitignored `data/results/`): the result JSONs below, the window diff, audit and verification JSONs, the v1 backup, and all raw caches.

| Local JSON | SHA-256 |
|---|---|
| `data/results/phase6_obs_coverage_v1.json` | `85eb44dffc8550f2e0bea86a649582a5726eb1627db7c2eb065b8dc9eb6ffc2a` |
| `data/results/phase6_operational_comparison_v1.json` | `07c045077edbaac386dfa3c827f7a433b71e1f88edcbc1d3555cf8870ef570d7` |
| `data/results/phase6_shadow_evaluation_v1.json` | `458580dfba30a5fcee87da90f1b537fbf14a55485dac2fadf0d906c5f21da4af` |
| `data/results/phase6_methodology_v1.json` | `c9c6b89b397969cf86c686c0af6617121f996fb427d5a52b4e618724abf443a2` |
| `data/results/phase6_obs_coverage_v2.json` | `153bdda70d742718bf6a219ca8de5178121a89c8f4da97de252a7085c1f6745f` |
| `data/results/phase6_operational_comparison_v2.json` | `89360587bf373240a1ac76766af7c4da883cd071f10ecfddc5149f96bd7a0a10` |
| `data/results/phase6_shadow_evaluation_v2.json` | `64ac37e736b9d829cacb00ad7f95300fb4fb3efc7b8828192625d1d5279402db` |
| `data/results/phase6_methodology_v2.json` | `8df00220deb6a53410e05ed0fd6c624528d97aae3ca40c5417c00786dd3286eb` |
| `data/results/phase6_obs_coverage_v2_1.json` | `74b563d830dbb2a81a30a6a4afd75b2e9044ad10ead41b676a7ec55a8bbe0c66` |
| `data/results/phase6_operational_comparison_v2_1.json` | `1a09e69c007cc843fa9d80bcb8536872218f18f5bbc03517f327c12515b85799` |
| `data/results/phase6_shadow_evaluation_v2_1.json` | `65a10b5e292c03e2d37897d84dd760f8fd1897a4a29e2e839d867be83b82fbd7` |
| `data/results/phase6_methodology_v2_1.json` | `cd1aeb54c249148904a3ac70a78e3e22ca77f49538d55aebbde49690e334a05c` |
| `data/results/phase6_window_fix_diff_v2.json` | `82cf4ac93fba1da3d36f91fab6250fd2e5898c0aaa35eff244337263551b71be` |
| `data/results/phase6_hrrr_d0_0900_coverage_audit_v2_1.json` | `c01264f7c05f79272e4f0fd523b881accc06de2aaf26db0ce4d60614d02624e5` |
| `data/results/phase6_v2_1_migration_verification.json` | `c0e2caa1a0d923f9842b70c2474d55ce7068bf4d0d5949643d7d609813c5d92a` |
