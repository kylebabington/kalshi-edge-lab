# Sports forecast-error diagnosis — frozen 2026-10-21 snapshot

**As of 2026-10-21T20:38:39.935499Z.** RESEARCH_ONLY / NO_BET. Descriptive only: no new significance gate, no protocol change, no profitability or promotion claim.

This note decomposes the sports frozen−market gap from the [October 21 prospective review](prospective_performance_review_2026-10-21.md). Weather Phase 7 is unchanged and is not part of this diagnosis. Collectors, models, scoring, and the frozen review were not modified.

Read-only script: [`scripts/sports_forecast_error_diagnosis.py`](../../scripts/sports_forecast_error_diagnosis.py). It hashes every used snapshot score against the review listing (`53abb221…1a51c8`) and does not fetch or rewrite records.

Companions: [`_summary.json`](sports_forecast_error_diagnosis_2026-10-21_summary.json), [`_contributions.csv`](sports_forecast_error_diagnosis_2026-10-21_contributions.csv), [`_bins.csv`](sports_forecast_error_diagnosis_2026-10-21_bins.csv), [`_cases.csv`](sports_forecast_error_diagnosis_2026-10-21_cases.csv), [`_fitted_maps.csv`](sports_forecast_error_diagnosis_2026-10-21_fitted_maps.csv).

Primary sample: **4,695** contracts / **2,828** events with both frozen and market probabilities and a confirmed binary settlement. Event-equal weight per matched contract is `1/(N · n_e)` with `N = 2,828`. Contract-weighted weight is `1/4,695`.

Frozen−market event-equal Brier: **+0.03206433177158735**. The frozen review reported +0.032064331771587344. Absolute difference `6.9e-18` is numerical agreement within `1e-12`, not bit-identical.

---

## Where the event-equal gap sits

Partition contributions sum to the event-equal aggregate (script `math.fsum`; see Independent checks). Sign is frozen minus market; positive means higher frozen Brier.

| Sport | Events | Contracts | Event-mean Δ Brier | Contribution to +0.03206 |
|---|---:|---:|---:|---:|
| Tennis | 1,432 | 1,432 | +0.0465 | **+0.02353** |
| Basketball | 259 | 269 | +0.0424 | +0.00388 |
| Volleyball | 82 | 82 | +0.0711 | +0.00206 |
| Cricket | 79 | 79 | +0.0395 | +0.00110 |
| Hockey | 222 | 482 | +0.0061 | +0.00048 |
| MMA | 16 | 16 | +0.0847 | +0.00048 |
| Football | 154 | 771 | +0.0079 | +0.00043 |
| Darts | 65 | 65 | +0.0173 | +0.00040 |
| Soccer | 186 | 1,132 | +0.0054 | +0.00035 |
| Rugby | 1 | 1 | +0.0225 | +0.00001 |
| Table Tennis | 42 | 42 | −0.0034 | −0.00005 |
| Baseball | 13 | 47 | −0.0153 | −0.00007 |
| Esports | 277 | 277 | −0.0055 | −0.00054 |

Tennis is 1,432 / 2,828 events and **0.02353 / 0.03206 (73%)** of the event-equal Brier gap. The rest of the gap is spread across many sports; Esports is slightly better than market on this sample.

Largest competition contributions (event-equal Δ Brier): `k_kxitfwmatch` +0.01068 (436 events), `k_kxitfmatch` +0.00706 (376), `k_kxatpchallengermatch` +0.00313 (218), `k_kxncaawvmatch` +0.00206 (82), `k_kxitfwdoubles` +0.00104 (78). ITF singles (`k_kxitfmatch` + `k_kxitfwmatch`) is **0.01774 / 0.03206 (55%)**. The full competition table is in the contributions CSV.

Family shares use the same event-equal weights (each contract contributes `Δ / (N · n_e)`). They sum to the same aggregate:

| Family | Events with family | Contracts | Contribution to event-equal Δ Brier |
|---|---:|---:|---:|
| game_winner | 2,803 | 2,803 | **+0.03153** |
| team_total | 262 | 523 | +0.00033 |
| total | 396 | 586 | +0.00021 |
| spread | 396 | 783 | −0.00001 |

**game_winner is 0.03153 / 0.03206 (98%)** of the primary gap. Spread is essentially flat. Segment and score_props never enter this pair: the market candidate is out of frozen scope there.

Log-loss contributions have the same ranking (Tennis +0.05580 of +0.07580 event-equal Δ log loss; game_winner +0.07447).

---

## Calibration bins

Ten equal-width bins on quoted `p`. Two weightings: contract-weighted (`1/N_contracts`) and event-equal (`1/(N · n_e)`). Unique-event counts are reported per bin (an event can appear in more than one bin). Loss shares use the same weights and reconcile to the matching aggregate.

Frozen `p`, event-equal (primary):

| Bin | Contracts | Unique events | Weight | Mean p | Observed freq | Overconfidence | Share of Δ Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| [0.0, 0.1] | 62 | 61 | 0.0038 | 0.069 | 0.059 | +0.010 | −0.00009 |
| [0.1, 0.2] | 285 | 240 | 0.0197 | 0.154 | 0.142 | +0.012 | +0.00015 |
| [0.2, 0.3] | 419 | 327 | 0.0399 | 0.256 | 0.291 | −0.034 | +0.00009 |
| [0.3, 0.4] | 591 | 483 | 0.0992 | 0.359 | 0.293 | +0.066 | +0.00354 |
| [0.4, 0.5] | 1,338 | 1,186 | 0.3449 | 0.458 | 0.430 | +0.028 | **+0.01267** |
| [0.5, 0.6] | 1,362 | 1,246 | 0.3696 | 0.536 | 0.552 | −0.016 | **+0.01309** |
| [0.6, 0.7] | 468 | 413 | 0.0957 | 0.638 | 0.720 | −0.081 | +0.00202 |
| [0.7, 0.8] | 124 | 113 | 0.0215 | 0.732 | 0.776 | −0.044 | +0.00049 |
| [0.8, 0.9] | 38 | 35 | 0.0051 | 0.829 | 0.754 | +0.074 | +0.00011 |
| [0.9, 1.0] | 8 | 8 | 0.0006 | 0.931 | 0.766 | +0.165 | +0.00000 |

Frozen puts **71.5%** of event-equal weight in [0.4, 0.6], and those two bins hold **0.02576 / 0.03206 (80%)** of the paired Brier gap. Frozen is overconfident in [0.3, 0.4] and in the thin right tail; underconfident in [0.6, 0.7]. Frozen extreme-miss count (`p < 0.05` and `y=1`, or `p > 0.95` and `y=0`) is 0, because frozen rarely quotes those tails.

Market `p`, event-equal:

| Bin | Contracts | Unique events | Weight | Mean p | Observed freq | Overconfidence | Share of Δ Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| [0.0, 0.1] | 238 | 234 | 0.0463 | 0.067 | 0.023 | +0.044 | **+0.00631** |
| [0.1, 0.2] | 459 | 408 | 0.0799 | 0.150 | 0.168 | −0.018 | +0.00497 |
| [0.2, 0.3] | 536 | 456 | 0.0928 | 0.252 | 0.243 | +0.009 | +0.00367 |
| [0.3, 0.4] | 661 | 530 | 0.1162 | 0.350 | 0.372 | −0.022 | +0.00135 |
| [0.4, 0.5] | 811 | 695 | 0.1700 | 0.457 | 0.465 | −0.008 | +0.00076 |
| [0.5, 0.6] | 1,006 | 861 | 0.2319 | 0.527 | 0.542 | −0.014 | +0.00035 |
| [0.6, 0.7] | 415 | 376 | 0.0938 | 0.643 | 0.626 | +0.017 | +0.00052 |
| [0.7, 0.8] | 291 | 278 | 0.0820 | 0.744 | 0.756 | −0.011 | +0.00374 |
| [0.8, 0.9] | 168 | 164 | 0.0512 | 0.849 | 0.904 | −0.056 | **+0.00577** |
| [0.9, 1.0] | 110 | 110 | 0.0359 | 0.934 | 0.945 | −0.010 | +0.00463 |

The market uses both tails (238 contracts in [0.0, 0.1] vs frozen 62; 110 in [0.9, 1.0] vs frozen 8). When the market quote is in [0.0, 0.2] or [0.8, 1.0], those bins hold **0.02168 / 0.03206 (68%)** of the paired Brier gap: frozen is closer to 0.5 and loses more on the same contracts. Market extreme-miss count is 1 + 1 in the two end bins.

Contract-weighted bins are in the CSV. They show the same mid-probability pile-up for frozen and the same market-tail concentration of the paired gap; the numeric shares sum to the contract-weighted Δ Brier +0.02177 rather than +0.03206.

Horizons on all 4,695 matched contracts: min 60.0 min, median 77.2, max 80.0. Market candle age: min 23 s, median 2,516 s (~42 min), max 10,652 s. None missing.

---

## Case review (sample frozen first)

The case list was selected from matched `(d_brier, ticker)` only, before any capture-record inspection: 10 largest positive frozen−market Brier differences, 10 largest negative, 20 uniform draws with seed `20261021`. Ties broken by ticker. Deduplicated; selection reasons retained. Result: **40 unique contracts**, no overlap.

After inspection of the saved score and the matching snapshot capture record:

| Classification | Count |
|---|---:|
| confirmed_defect | 0 |
| ordinary_incorrect_prediction | 40 |
| cannot_verify | 0 |

What the saved evidence verifies on these 40: ticker present on the capture record; `source_record_sha256` matches; window timestamps satisfy `window_open ≤ prediction_as_of ≤ finalized_at ≤ scheduled_cutoff`; market `candle_end` is not after `prediction_as_of`; `age_s` equals that difference; market `p` is the two-sided bid/ask mid.

What it does not verify: `as_of_view` on the saved record is a summary (`results_used` / `events_hidden` / `latest_release_used`). It cannot prove every unavailable outcome field was cleared. That rule lives in `research.sports.live.sources.hide` and is a code-inspection fact, not a record fact. Each case note states this limit. No fresh download was used.

Largest positive differences (frozen loses most vs market) include NCAAF team_total `KXNCAAFTEAMTOTAL-26OCT17ELONSTAN-ELON24` (frozen 0.735 vs market 0.085, `y=0`) and several ITF/WTA/ATP-challenger game_winner quotes where frozen was 0.61–0.71 and the market was much lower (or vice versa) and the market side settled. Six of the ten are tennis game_winner.

Largest negative differences (frozen beats market most) are a comparison set, not a defect hunt: eight of ten are ITF game_winner where the market was extreme and wrong (example: `KXITFWMATCH-26OCT14MAZBUL-BUL`, frozen 0.438 vs market 0.98, `y=0`). Frozen near 0.5 won those contracts; the average still goes the other way because market tails are more often right.

---

## Pinned maps, read from the protocol

Parameters come from pinned `research/sports/protocols/sports_phase2_v1.json`. File sha256 **`b95e662b233931caafbedfa16b04081d3c9ca01a2e43abfafa0c81867c81cb39`**, verified before reading. Full-precision coefficients are in [`_fitted_maps.csv`](sports_forecast_error_diagnosis_2026-10-21_fitted_maps.csv). They are not rounded report figures.

Status `ok` calibration groups on this snapshot: `k_kxatpmatch|game_winner` (identity, `a=0`, `b=1`), `k_kxwtamatch|game_winner` (platt, λ=10, `a=3.9077821423716e-18`, `b=1.2231934886862383`), `ncaaf|game_winner` (identity), `ncaaf|spread` (intercept, λ=10, `a=0.1596999662007691`, `b=1`), `ncaaf|total` (platt, λ=1, `a=-0.11503499374543573`, `b=0.9042893609144778`).

Status `ok` blend groups: `ncaaf|game_winner` (`w=0.3`, `c=0`), `ncaaf|spread` (`w=0`, `c=-0.10320452271443377`, λ=0), `ncaaf|total` (`w=0`, `c=0`). ATP/WTA blends are unavailable (matched training support below threshold). Other sports have no `ok` map.

Paired losses on this prospective sample (event-equal Brier unless noted):

| Slice | Events | vs frozen | vs market |
|---|---:|---:|---:|
| Identity calibration (ATP GW + NCAAF GW) | 180 | ~0 (copy) | — |
| Non-identity calibration (WTA GW + NCAAF spread/total) | 201 | **+0.00450** | — |
| NCAAF total blend (`w=0`, `c=0`, market copy) | 123 | −0.00554 | ~0 |
| NCAAF spread blend (`w=0`, intercept only) | 123 | +0.00710 | −0.00261 |
| NCAAF game_winner blend (`w=0.3`) | 121 | −0.01129 | +0.00091 |

Identity maps are copies of frozen. Non-identity historical calibration is slightly **worse** than frozen here. The NCAAF `w=0.3` blend beats frozen on 121 events and is slightly worse than market. None of these slices is tennis ITF, so none of them addresses the 73% tennis share of the primary gap. Maps were not retuned on this sample.

---

## Ranked recommendations (descriptive)

1. **The primary gap is two-way tennis game-winner, especially ITF.** Tennis contributes 0.02353 of +0.03206; ITF singles alone 0.01774; game_winner 0.03153. Any later work that does not move that slice cannot move the event-equal number. This is a location finding, not a change to the frozen protocol.

2. **Frozen probabilities sit near 0.5 while the market uses tails; the pinned historical maps do not close that gap.** Event-equal [0.4, 0.6] holds 80% of the paired Brier gap. Market-tail bins hold 68%. Recalibrating toward market-like tails is what the bins suggest, but identity maps add nothing and non-identity maps raise event-equal Brier by +0.0045 on 201 events. Do not fit new maps on this frozen sample.

3. **Do not treat the gap as a quote or as-of bug.** The 40-case review found 0 confirmed input defects. Horizons are inside the 60–80 minute window; candle ages are present. Negative cases show the market tail can be wrong, which is why they were included. `as_of_view` still cannot prove `hide()`; that remains a code-inspection limit.

---

## Independent checks

Stable summation is `math.fsum`. Tolerance `1e-12`. Numerical agreement within tolerance is reported separately from bit-for-bit equality.

- Script self-checks (in `_summary.json`): event-equal weights sum to 1 bit-identically; family shares and event-equal bin Δ-Brier shares are bit-identical to the aggregate; sport/competition contributions and the review delta agree numerically (`6.9e-18`), not bit-identically. Missing essential score fields (`ticker`, `event_key`, `sport`, `competition`, `family`, `y`) would have stopped the run; none were missing.
- Independent rescan of snapshot finalized scores with the frozen `_losses` helper, not through the diagnosis module: 4,695 contracts / 2,828 events; event-equal Δ Brier **bit-identical** to the review value `0.032064331771587344`; sport-contribution sum agrees numerically (`6.9e-18`), not bit-identically. Tennis contribution `0.02353254516714668`.
- Published CSV columns, parsed at 12 significant digits, sum to the same aggregates within `1e-12` and are not bit-identical (expected rounding). Case table: 40 unique tickers, 10 / 10 / 20 reasons, all `ordinary_incorrect_prediction`.

No capture or score record was rewritten. No network fetch.
