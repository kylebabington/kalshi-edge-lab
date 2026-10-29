# ITF singles challenger study v1

**As of 2026-10-23T00:34:18.095413Z.** RESEARCH_ONLY / NO_BET. Descriptive only: no profitability, promotion, or executable-edge claim.

This note continues from the [October 21 forecast-error diagnosis](sports_forecast_error_diagnosis_2026-10-21.md). Frozen models, capture protocols, scoring, collectors, schedules, accepted records, the October 21 review, and weather files were not modified.

Runner: `python -m research.sports.challenger.run`. Companions: [`_summary.json`](sports_itf_challenger_study_v1_summary.json), [`_replay.csv`](sports_itf_challenger_study_v1_replay.csv), [`_partitions.csv`](sports_itf_challenger_study_v1_partitions.csv), [`_identity.csv`](sports_itf_challenger_study_v1_identity.csv), [`_sources.csv`](sports_itf_challenger_study_v1_sources.csv). Challenger protocol: [`research/sports/challenger/protocol_itf_singles_v1.json`](../../research/sports/challenger/protocol_itf_singles_v1.json).

## Verified inputs

Complete snapshot listing payload sha256 `53abb22116a0a4194c92097975387914a02d4747ed95a42f4c38a4edce1a51c8`. Capture protocol `6fce0fac0623241f373ec2f277c167030075d2a8af73bf1540681f56c5fb41ad`. Phase 1 protocol `66283d9e2a6b679d4391a53d195d122269fbeed62a4c76f24a77e7dae4d0f4c6`. Phase 2 protocol `b95e662b233931caafbedfa16b04081d3c9ca01a2e43abfafa0c81867c81cb39`. Phase 1 / Phase 2 / live code hashes match the frozen capture pins.

Frozen normalized history used for replay (local files hashed against those pins; a mismatch stops the affected replay):

| Competition | events.jsonl | contracts.jsonl |
|---|---|---|
| k_kxitfmatch | `910755b9c55ddedb3761e83f51e6808c7402b3e054b647c59e5651c7f887c81e` | `e39dd310fed0ed01bf60b748220f2d8e51797f30c4950f8ab1f0f6960cf6da8d` |
| k_kxitfwmatch | `543116b42078b0d4de41478e53b9db268545ee4bc9738b8f5dce23baeaab3a7b` | `109420609600205fcebda0a079410821f437a7ff8ca19e20eaf38e1c354eb495` |

Capture-time history was recovered from the snapshot capture record plus evidence bodies whose sha256 matches that record. Evidence `received_at` is not after `prediction_as_of`. Settlement time alone was not used to add results. Live events were deduplicated by `source_game_id`. `as_of_view.results_used` matched the saved capture record on all 812 replayed contracts.

Documented reconstruction tolerance: `|p_replay − frozen_p| ≤ 1e-12` after mapping each ticker to `elo.prob(participant, opponent)` with `participant` from the capture record. Interleaving matches `models._konly_run` (releases by `available_at`, query at start − 60 min, rating time = start).

## ITF totals versus the diagnosis

Event-equal frozen−market Brier contributions use the diagnosis event count N = 2828:

| Competition | Events | Contribution | Diagnosis target | Agreement (1e-9) |
|---|---:|---:|---:|---|
| k_kxitfmatch | 376 | 0.00706402233099966 | 0.007064022331 | yes |
| k_kxitfwmatch | 436 | 0.010676471975440437 | 0.0106764719754 | yes |
| ITF singles | 812 | 0.017740494306440098 | 0.0177404943064 | yes |

Matched replay rows: **812**. Capture-time history recovered: **812**. Capture-time and p-agree within 1e-12: **812**.

## Replay status

Counts and ratings below are **capture-time**, not reconstructed. A reconstructed n=0 would not have established a live cold start; that case did not occur here.

| Slice | Contracts | Event-equal Δ Brier | Mean \|rating d\| | Mean \|p−0.5\| |
|---|---:|---:|---:|---:|
| capture_time_agree | 812 | 0.017740494306440098 | 48.66 | 0.0683 |
| min_n = 0 | 69 | 0.0025381277763126603 | 29.18 | 0.0415 |
| min_n = 1–2 | 93 | 0.0029389967357472705 | 36.87 | 0.0522 |
| min_n = 3–9 | 226 | 0.004851728737466848 | 47.71 | 0.0671 |
| min_n = 10–29 | 394 | 0.007388381028260303 | 55.47 | 0.0777 |
| min_n ≥ 30 | 30 | 0.00002326002865301545 | 47.76 | 0.0673 |
| \|d\| < 20 | 215 | 0.006105339156024894 | 9.41 | 0.0135 |
| \|d\| = 20–50 | 289 | 0.007363905910069431 | 33.35 | 0.0478 |
| \|d\| = 50–100 | 214 | 0.0022681643037103963 | 70.69 | 0.1002 |
| \|d\| ≥ 100 | 94 | 0.002003084936635375 | 135.36 | 0.1843 |

Most of the ITF event-equal gap sits on matches where both players already had Kalshi results (`min_n ≥ 3` contributes 0.01226 of 0.01774). Frozen mean \|p−0.5\| is 0.068. `carry=0` disables Elo decay; it is not shrinkage toward 50%. The 1500 base is the unseen-player prior. These figures describe rating separation and quoted probabilities. They do not establish that k=24 is too small.

## Surface and identity

Frozen event fields: `available_at`, `away`, `completed`, `home`, `neutral`, `source_game_id`, `sport`, `start`, `winner_id`. No surface field on frozen events or on capture contracts. Absent surface is an input limitation, not a demonstrated cause of the Brier gap.

Structured targets (discovery dump `20261008T2225Z`) loaded. Snapshot capture UUIDs: 1068 named, 84 missing from that dump, 0 exact-name collisions. Name collisions would be review candidates and would not be merged.

## Free-source probe (observed)

Twelve cached public GETs. No paid service. ITF World Tennis Tour coverage is read from the response.

| source_id | HTTP | Observation |
|---|---:|---|
| github_jeffsackmann_tennis_atp | 404 | GitHub API: repository not found |
| github_jeffsackmann_tennis_wta | 404 | GitHub API: repository not found |
| github_jeffsackmann_search_itf | 200 | `total_count` 0 for `tennis_itf` under JeffSackmann |
| sackmann_atp_matches_2024_raw | 404 | raw CSV not found |
| espn_tennis_itf_scoreboard | 400 | `Failed to get events endpoint` |
| espn_tennis_atp_scoreboard | 200 | ATP JSON; player-id token present; no ITF token in the sampled body |
| espn_tennis_wta_scoreboard | 200 | WTA JSON; player-id token present; no ITF token in the sampled body |
| espn_tennis_challenger_scoreboard | 400 | `Failed to get events endpoint` |
| itftennis_mens_calendar | 200 | Incapsula HTML challenge page, not a results feed |
| itftennis_rankings_men | 200 | Incapsula HTML challenge page, not a results feed |
| tennis_data_co_uk_2024 | 403 | HTML challenge / forbidden |
| tennis_data_co_uk_notes | 403 | HTML challenge / forbidden |

Usable ITF result feeds under the study rule (HTTP 200, not HTML-only, ITF token and player-id token both present): **none**.

## Challenger

Capture-time replay agreed on 812/812 matched contracts, so a Kalshi-history challenger is supported. Independent ITF feeds did not pass the usable-feed rule, and exact-name equality is not used to merge UUIDs.

Selection rules were recorded in `research/sports/challenger/itf_singles_v1.py` `selection_rules()` before the grid was scored: chronological window 2026-07-01 ≤ start < 2026-10-01 on verified frozen events; candidates k ∈ {12,24,36,48} and carry ∈ {0.0, 0.33}; metric = mean log loss of P(home wins); tie-break = lower metric, then lower k, then lower carry; Elo state separate per competition; October 21 snapshot outcomes not used.

Selected: **k=48, carry=0.0**, select log loss 0.6555035981567607 on 15,264 frozen events (two pools). Same window, k=24 carry=0.0 logged 0.6658576558981829. The live frozen model remains k=24 carry=0.0.

Protocol frozen_at **2026-10-23T00:34:18.095413Z**. Challenger code hash `8ffda605555b1aeb962a6e491975d3c0ff18ff61e7ec2235474c8f798b2a79b3`. Not imported by the collector, scheduler, or `live/predict.py`.

October 21 snapshot outcomes remain development data. Replaying any event after 2026-10-21T20:38:39.935499Z at study time is also development analysis, even if those outcomes have not previously been scored.

## Prospective rule (starts after this freeze)

Eligible: `k_kxitfmatch` and `k_kxitfwmatch` predictions **recorded under this frozen challenger protocol** with `prediction_as_of` after `2026-10-23T00:34:18.095413Z`, made before the outcome was available. Metric: event-equal Brier versus the frozen live candidate and versus the capture market quote. No new significance gate. RESEARCH_ONLY / NO_BET.

## Next step

Collect those prospective records after the freeze timestamp without connecting the scheduled sports collector. Do not score post-2026-10-21 replays run now as prospective.

Manual collection is documented in [sports_itf_challenger_capture_v1.md](sports_itf_challenger_capture_v1.md). The active capture CLI uses wrapper v2; the v1 wrapper protocol, study protocol, and helper were not re-frozen.
