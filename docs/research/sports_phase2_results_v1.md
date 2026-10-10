# Sports Phase 2 results (sports_phase2_v1)

RESEARCH_ONLY / NO_BET. Retrospective development results; no trades, promotion or profitability claims.

> 2026 (EVAL) was already examined in Phase 1 (v1, v2). Phase 2 scores on it are retrospective development results, not confirmation; no candidate is selected on 2026.

## What is frozen

- Phase 1 models, protocols and results are read-only (Phase 1 code hash `5672164410b0a027...`, `sports_phase1_v2` research artifacts verified byte-exact before every stage).
- Phase 2 protocol `research/sports/protocols/sports_phase2_v1.json` (sha256 `b95e662b233931ca...`) holds the periods, boundaries, candidate definitions, calibration/blend grids, optimizer, thresholds, tie-breaks, the fitted parameters per group, cohort rules, comparisons, sufficiency, bootstrap and the market-support table; it was written before any 2026 scoring.
- Market sample manifest sha256 `16a86d3dc80662cf...` frozen before any request.

## Periods

| period | range (UTC) | role |
|---|---|---|
| F1 | 2025-01-01 to 2025-07-01 | train |
| F2 | 2025-07-01 to 2025-10-01 | train |
| F3 | 2025-10-01 to 2026-01-01 | validation |
| EVAL | 2026-01-01 to 2026-10-06 | evaluation |

## Coverage across the supported universe

Every one of the 3871 Kalshi sports series in the Phase 1 universe appears in `docs/research/sports_phase2_coverage_v1.csv` with its Phase 1 status and Phase 2 status.

| Phase 2 status (per series) | series |
|---|---|
| phase1:not_attempted_supported_sport | 2915 |
| calibration_unavailable+blend_unavailable | 419 |
| phase1:excluded_spec | 251 |
| phase1:no_test_contracts | 154 |
| phase1:not_attempted_unsupported_sport | 123 |
| phase1:excluded_no_reconciled_contracts | 4 |
| calibrated_ok+blend_ok | 3 |
| calibrated_ok+blend_unavailable | 2 |

Out-of-fold folds per competition (all 313 attempted competitions):

| fold | status | competitions |
|---|---|---|
| F1 | no contracts in fold | 297 |
| F1 | ok | 16 |
| F2 | no contracts in fold | 298 |
| F2 | ok | 15 |
| F3 | no contracts in fold | 268 |
| F3 | ok | 45 |

Calibration groups (competition x family): 268.

| calibration status | groups |
|---|---|
| unavailable: training support below threshold | 260 |
| ok | 5 |
| unavailable: validation support below threshold | 3 |

Selected calibration forms (available groups): identity 2, intercept 1, platt 2

| competition | family | train events | validation events | form | lambda | a | b | F3 event-equal log loss: identity | selected |
|---|---|---|---|---|---|---|---|---|---|
| k_kxatpmatch | game_winner | 842 | 372 | identity | 0.0 | 0.0000 | 1.0000 | 0.65279 | 0.65279 |
| k_kxwtamatch | game_winner | 891 | 282 | platt | 10.0 | 0.0000 | 1.2232 | 0.65145 | 0.65113 |
| ncaaf | game_winner | 364 | 561 | identity | 0.0 | 0.0000 | 1.0000 | 0.54605 | 0.54605 |
| ncaaf | spread | 223 | 532 | intercept | 10.0 | 0.1597 | 1.0000 | 0.64706 | 0.64422 |
| ncaaf | total | 223 | 535 | platt | 1.0 | -0.1150 | 0.9043 | 0.64032 | 0.63835 |

When identity is selected the calibrated candidate equals the frozen model and the calibrated-frozen difference is exactly zero.

| blend status | groups |
|---|---|
| unavailable: matched training support below threshold | 220 |
| unavailable: segment markets (quarters/halves/innings): no new historical quotes by scope decision | 22 |
| unavailable: score props: no new historical quotes by scope decision | 18 |
| ok | 3 |
| unavailable: matched validation support below threshold | 3 |
| unavailable: field finish (golf/F1): no new historical quotes by scope decision | 2 |

| competition | family | matched train events | matched validation events | w (model) | lambda | c |
|---|---|---|---|---|---|---|
| ncaaf | game_winner | 279 | 197 | 0.3 | None | 0.0000 |
| ncaaf | spread | 204 | 199 | 0.0 | 0.0 | -0.1032 |
| ncaaf | total | 217 | 199 | 0.0 | None | 0.0000 |

No new historical quotes were fetched for segments, score props, field finish or player props; their market and blend candidates are unavailable with that reason.

## Historical market sample and fetch

- Frozen events: 35792; frozen uncached unique requests: 42037 (budget 60000, retry cap 5000, per-request retries 4).
- Pre-fetch estimate: lower bound 5.84 h at 0.5 s spacing (retries and slow responses extend it).
- Fetch ledger: 46177 entries, 42037 unique network requests, 0 retries, statuses {'200': 42037, 'cached': 4140}.
- Tier truncation: 1_gw_train_validation: kept 5445/5445; 2_spread_total: kept 12528/12528; 3_team_total: kept 1800/1800; 4_gw_evaluation: kept 16019/16019
- Preserved Phase 1 benchmark sample kept as its own stratum: listing status {'listed_after_cutoff': 133, 'listed_by_cutoff': 6068}.

| stratum | period | quote status | contracts |
|---|---|---|---|
| phase1_v1_sample | evaluation | no candle ended by cutoff | 142 |
| phase1_v1_sample | evaluation | no two-sided quote in last ended candle | 64 |
| phase1_v1_sample | evaluation | ok | 5941 |
| phase1_v1_sample | evaluation | stale | 54 |
| phase2_sample | evaluation | no candle ended by cutoff | 94 |
| phase2_sample | evaluation | no two-sided quote in last ended candle | 250 |
| phase2_sample | evaluation | ok | 35504 |
| phase2_sample | evaluation | stale | 287 |
| phase2_sample | train | no candle ended by cutoff | 50 |
| phase2_sample | train | no two-sided quote in last ended candle | 73 |
| phase2_sample | train | ok | 2895 |
| phase2_sample | train | stale | 17 |
| phase2_sample | validation | no candle ended by cutoff | 64 |
| phase2_sample | validation | no two-sided quote in last ended candle | 77 |
| phase2_sample | validation | ok | 6829 |
| phase2_sample | validation | stale | 37 |

## 2026 retrospective comparisons (competition level)

Differences are a - b (negative = a better). Classes use the 95% cluster-bootstrap interval of the Brier difference and are exploratory and unadjusted for multiple comparisons. Only comparisons whose final matched sample reaches the minimum (50 event clusters; field finish 15) are classified; the rest are descriptive.

| comparison | stratum | cohort | groups | sufficient | contract-weighted better/unclear/worse | event-equal better/unclear/worse |
|---|---|---|---|---|---|---|
| calibrated-frozen | all | strict | 3 | 3 | 0/2/1 | 0/2/1 |
| calibrated-frozen | all | kalshi_settlement_only | 2 | 2 | 0/1/1 | 0/1/1 |
| frozen-market | phase2_sample | strict | 47 | 46 | 0/28/18 | 0/28/18 |
| frozen-market | phase2_sample | exploratory | 41 | 32 | 0/21/11 | 0/21/11 |
| frozen-market | phase2_sample | kalshi_settlement_only | 129 | 69 | 1/28/40 | 1/28/40 |
| blend-market | phase2_sample | strict | 3 | 3 | 0/1/2 | 0/1/2 |
| blend-frozen | phase2_sample | strict | 3 | 3 | 3/0/0 | 3/0/0 |
| frozen-market | phase1_v1_sample | strict | 21 | 21 | 0/16/5 | 0/16/5 |
| frozen-market | phase1_v1_sample | exploratory | 2 | 2 | 0/2/0 | 0/2/0 |
| frozen-market | phase1_v1_sample | kalshi_settlement_only | 70 | 0 | 0/0/0 | 0/0/0 |
| blend-market | phase1_v1_sample | strict | 1 | 1 | 0/1/0 | 0/1/0 |
| blend-frozen | phase1_v1_sample | strict | 1 | 1 | 1/0/0 | 1/0/0 |

Sufficient competition-level comparisons:

| competition | family | cohort | comparison | stratum | clusters | d_brier (contract) [95% CI] | d_brier (event-equal) [95% CI] |
|---|---|---|---|---|---|---|---|
| argentina | game_winner | exploratory | frozen-market | phase2_sample | 300 | +0.0087 [+0.0007, +0.0170] | +0.0087 [+0.0007, +0.0170] |
| argentina | game_winner | exploratory | frozen-market | phase1_v1_sample | 150 | +0.0083 [-0.0032, +0.0203] | +0.0083 [-0.0032, +0.0203] |
| argentina | spread | exploratory | frozen-market | phase2_sample | 170 | +0.0014 [-0.0019, +0.0049] | +0.0014 [-0.0019, +0.0049] |
| argentina | total | exploratory | frozen-market | phase2_sample | 170 | +0.0004 [-0.0035, +0.0040] | +0.0004 [-0.0035, +0.0040] |
| brasileiro | game_winner | strict | frozen-market | phase2_sample | 279 | +0.0111 [+0.0008, +0.0216] | +0.0111 [+0.0008, +0.0216] |
| brasileiro | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0087 [-0.0053, +0.0225] | +0.0087 [-0.0053, +0.0225] |
| brasileiro | spread | exploratory | frozen-market | phase2_sample | 254 | +0.0053 [+0.0007, +0.0101] | +0.0056 [+0.0008, +0.0105] |
| brasileiro | team_total | exploratory | frozen-market | phase2_sample | 100 | +0.0097 [+0.0011, +0.0183] | +0.0097 [+0.0011, +0.0183] |
| brasileiro | total | exploratory | frozen-market | phase2_sample | 254 | +0.0035 [-0.0035, +0.0106] | +0.0034 [-0.0035, +0.0105] |
| bundesliga | game_winner | strict | frozen-market | phase2_sample | 207 | +0.0036 [-0.0058, +0.0118] | +0.0036 [-0.0058, +0.0118] |
| bundesliga | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0038 [-0.0072, +0.0140] | +0.0038 [-0.0072, +0.0140] |
| bundesliga | spread | strict | frozen-market | phase2_sample | 207 | +0.0021 [-0.0032, +0.0073] | +0.0026 [-0.0027, +0.0079] |
| bundesliga | total | strict | frozen-market | phase2_sample | 207 | +0.0062 [-0.0012, +0.0135] | +0.0051 [-0.0020, +0.0129] |
| bundesliga2 | game_winner | exploratory | frozen-market | phase2_sample | 171 | -0.0032 [-0.0154, +0.0093] | -0.0032 [-0.0154, +0.0093] |
| bundesliga2 | game_winner | exploratory | frozen-market | phase1_v1_sample | 150 | -0.0086 [-0.0221, +0.0043] | -0.0086 [-0.0221, +0.0043] |
| bundesliga2 | spread | exploratory | frozen-market | phase2_sample | 54 | +0.0049 [-0.0050, +0.0153] | +0.0049 [-0.0050, +0.0153] |
| bundesliga2 | total | exploratory | frozen-market | phase2_sample | 54 | +0.0010 [-0.0086, +0.0108] | +0.0010 [-0.0086, +0.0108] |
| eflc | spread | exploratory | frozen-market | phase2_sample | 124 | +0.0084 [+0.0022, +0.0159] | +0.0084 [+0.0022, +0.0159] |
| eflc | total | exploratory | frozen-market | phase2_sample | 124 | +0.0119 [+0.0035, +0.0203] | +0.0119 [+0.0035, +0.0203] |
| epl | game_winner | strict | frozen-market | phase2_sample | 244 | +0.0074 [-0.0035, +0.0180] | +0.0074 [-0.0035, +0.0180] |
| epl | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0092 [-0.0052, +0.0231] | +0.0092 [-0.0052, +0.0231] |
| epl | spread | strict | frozen-market | phase2_sample | 244 | +0.0017 [-0.0033, +0.0067] | +0.0016 [-0.0036, +0.0067] |
| epl | team_total | exploratory | frozen-market | phase2_sample | 69 | -0.0049 [-0.0200, +0.0098] | -0.0049 [-0.0200, +0.0098] |
| epl | total | strict | frozen-market | phase2_sample | 244 | +0.0122 [+0.0044, +0.0198] | +0.0111 [+0.0032, +0.0191] |
| eredivisie | game_winner | strict | frozen-market | phase2_sample | 215 | +0.0003 [-0.0104, +0.0106] | +0.0003 [-0.0104, +0.0106] |
| eredivisie | game_winner | strict | frozen-market | phase1_v1_sample | 149 | +0.0031 [-0.0089, +0.0155] | +0.0031 [-0.0089, +0.0155] |
| eredivisie | spread | exploratory | frozen-market | phase2_sample | 99 | +0.0052 [-0.0060, +0.0162] | +0.0054 [-0.0061, +0.0170] |
| eredivisie | total | exploratory | frozen-market | phase2_sample | 98 | +0.0099 [-0.0038, +0.0243] | +0.0099 [-0.0038, +0.0243] |
| k_kxabagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 116 | +0.0572 [+0.0294, +0.0860] | +0.0572 [+0.0294, +0.0860] |
| k_kxacbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0206 [-0.0068, +0.0469] | +0.0206 [-0.0068, +0.0469] |
| k_kxaflgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0508 [+0.0302, +0.0709] | +0.0508 [+0.0302, +0.0709] |
| k_kxahlgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | -0.0039 [-0.0182, +0.0098] | -0.0039 [-0.0182, +0.0098] |
| k_kxarglnbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 148 | +0.0260 [-0.0024, +0.0544] | +0.0260 [-0.0024, +0.0544] |
| k_kxatpchallengerdoubles | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 115 | +0.0181 [-0.0052, +0.0404] | +0.0181 [-0.0052, +0.0404] |
| k_kxatpchallengermatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0405 [+0.0121, +0.0671] | +0.0405 [+0.0121, +0.0671] |
| k_kxatpdoubles | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 145 | +0.0208 [-0.0021, +0.0427] | +0.0208 [-0.0021, +0.0427] |
| k_kxatpmatch | game_winner | kalshi_settlement_only | calibrated-frozen | all | 3356 | +0.0000 [-0.0000, +0.0000] | +0.0000 [-0.0000, +0.0000] |
| k_kxatpmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0283 [+0.0049, +0.0516] | +0.0283 [+0.0049, +0.0516] |
| k_kxbblgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0339 [+0.0100, +0.0575] | +0.0339 [+0.0100, +0.0575] |
| k_kxbbserieagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 123 | +0.0470 [+0.0223, +0.0701] | +0.0470 [+0.0223, +0.0701] |
| k_kxbslgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 123 | +0.0480 [+0.0196, +0.0763] | +0.0480 [+0.0196, +0.0763] |
| k_kxbsngame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 127 | +0.0142 [-0.0062, +0.0349] | +0.0142 [-0.0062, +0.0349] |
| k_kxcbagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0241 [+0.0005, +0.0459] | +0.0241 [+0.0005, +0.0459] |
| k_kxcflgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 68 | +0.0293 [+0.0010, +0.0532] | +0.0293 [+0.0010, +0.0532] |
| k_kxcodgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 145 | +0.0168 [-0.0050, +0.0371] | +0.0168 [-0.0050, +0.0371] |
| k_kxcs2game | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 143 | +0.0173 [-0.0077, +0.0409] | +0.0173 [-0.0077, +0.0409] |
| k_kxdartsmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 133 | +0.0123 [-0.0081, +0.0318] | +0.0123 [-0.0081, +0.0318] |
| k_kxdaviscupmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 51 | +0.1261 [+0.0847, +0.1631] | +0.1261 [+0.0847, +0.1631] |
| k_kxdelgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 138 | +0.0203 [-0.0001, +0.0399] | +0.0203 [-0.0001, +0.0399] |
| k_kxdota2game | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0265 [+0.0037, +0.0496] | +0.0265 [+0.0037, +0.0496] |
| k_kxelhgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 137 | +0.0322 [+0.0143, +0.0498] | +0.0322 [+0.0143, +0.0498] |
| k_kxeurocupgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 89 | +0.0534 [+0.0173, +0.0897] | +0.0534 [+0.0173, +0.0897] |
| k_kxeuroleaguegame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0148 [-0.0071, +0.0369] | +0.0148 [-0.0071, +0.0369] |
| k_kxfibagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 148 | +0.0814 [+0.0464, +0.1117] | +0.0814 [+0.0464, +0.1117] |
| k_kxgblgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 63 | +0.1061 [+0.0692, +0.1375] | +0.1061 [+0.0692, +0.1375] |
| k_kxiihfgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 64 | +0.0794 [+0.0268, +0.1264] | +0.0794 [+0.0268, +0.1264] |
| k_kxiplgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 72 | -0.0059 [-0.0183, +0.0066] | -0.0059 [-0.0183, +0.0066] |
| k_kxislgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 92 | +0.0173 [-0.0211, +0.0543] | +0.0173 [-0.0211, +0.0543] |
| k_kxitfdoubles | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 137 | +0.0116 [-0.0134, +0.0348] | +0.0116 [-0.0134, +0.0348] |
| k_kxitfmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 139 | +0.0594 [+0.0355, +0.0837] | +0.0594 [+0.0355, +0.0837] |
| k_kxitfwdoubles | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 133 | +0.0089 [-0.0127, +0.0299] | +0.0089 [-0.0127, +0.0299] |
| k_kxitfwmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 139 | +0.0480 [+0.0196, +0.0729] | +0.0480 [+0.0196, +0.0729] |
| k_kxjbleaguegame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0433 [+0.0208, +0.0662] | +0.0433 [+0.0208, +0.0662] |
| k_kxkblgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 132 | +0.0198 [+0.0001, +0.0387] | +0.0198 [+0.0001, +0.0387] |
| k_kxkbogame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0001 [-0.0139, +0.0141] | +0.0001 [-0.0139, +0.0141] |
| k_kxkhlgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 148 | +0.0234 [+0.0049, +0.0401] | +0.0234 [+0.0049, +0.0401] |
| k_kxliigagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 142 | +0.0063 [-0.0161, +0.0280] | +0.0063 [-0.0161, +0.0280] |
| k_kxlmbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0066 [-0.0071, +0.0198] | +0.0066 [-0.0071, +0.0198] |
| k_kxlnbelitegame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 147 | +0.0483 [+0.0228, +0.0745] | +0.0483 [+0.0228, +0.0745] |
| k_kxlnbpgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0153 [-0.0045, +0.0339] | +0.0153 [-0.0045, +0.0339] |
| k_kxlolgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0333 [+0.0069, +0.0574] | +0.0333 [+0.0069, +0.0574] |
| k_kxnbasummergame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 93 | -0.0015 [-0.0273, +0.0215] | -0.0015 [-0.0273, +0.0215] |
| k_kxnblgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 92 | +0.0238 [-0.0017, +0.0484] | +0.0238 [-0.0017, +0.0484] |
| k_kxncaabbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 139 | +0.0194 [+0.0008, +0.0373] | +0.0194 [+0.0008, +0.0373] |
| k_kxncaahockeygame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 53 | +0.0256 [-0.0012, +0.0529] | +0.0256 [-0.0012, +0.0529] |
| k_kxncaamlaxgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 148 | +0.0767 [+0.0481, +0.1028] | +0.0767 [+0.0481, +0.1028] |
| k_kxncaawvmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 138 | +0.0470 [+0.0204, +0.0740] | +0.0470 [+0.0204, +0.0740] |
| k_kxnlgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 137 | -0.0011 [-0.0238, +0.0222] | -0.0011 [-0.0238, +0.0222] |
| k_kxnpbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0123 [-0.0032, +0.0277] | +0.0123 [-0.0032, +0.0277] |
| k_kxnznblgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 100 | +0.0505 [+0.0258, +0.0743] | +0.0505 [+0.0258, +0.0743] |
| k_kxodimatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 132 | +0.0294 [+0.0104, +0.0482] | +0.0294 [+0.0104, +0.0482] |
| k_kxowgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 145 | +0.0872 [+0.0596, +0.1105] | +0.0872 [+0.0596, +0.1105] |
| k_kxpllgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 53 | +0.0003 [-0.0121, +0.0130] | +0.0003 [-0.0121, +0.0130] |
| k_kxr6game | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0289 [+0.0039, +0.0533] | +0.0289 [+0.0039, +0.0533] |
| k_kxshlgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 144 | -0.0005 [-0.0232, +0.0204] | -0.0005 [-0.0232, +0.0204] |
| k_kxsquashmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 72 | +0.0450 [+0.0181, +0.0726] | +0.0450 [+0.0181, +0.0726] |
| k_kxt20match | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 146 | +0.0254 [+0.0074, +0.0450] | +0.0254 [+0.0074, +0.0450] |
| k_kxttelitematch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 122 | -0.0126 [-0.0399, +0.0154] | -0.0126 [-0.0399, +0.0154] |
| k_kxttmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 117 | -0.0172 [-0.0267, -0.0075] | -0.0172 [-0.0267, -0.0075] |
| k_kxttstarmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 129 | +0.0173 [-0.0010, +0.0352] | +0.0173 [-0.0010, +0.0352] |
| k_kxvalorantgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 148 | +0.0315 [+0.0109, +0.0539] | +0.0315 [+0.0109, +0.0539] |
| k_kxvbagame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 63 | +0.0466 [+0.0144, +0.0777] | +0.0466 [+0.0144, +0.0777] |
| k_kxvtbgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 118 | +0.0693 [+0.0470, +0.0900] | +0.0693 [+0.0470, +0.0900] |
| k_kxwbcgame | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 74 | +0.0638 [+0.0176, +0.1057] | +0.0638 [+0.0176, +0.1057] |
| k_kxwt20match | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 145 | +0.0535 [+0.0328, +0.0742] | +0.0535 [+0.0328, +0.0742] |
| k_kxwtachallengermatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 150 | +0.0408 [+0.0114, +0.0689] | +0.0408 [+0.0114, +0.0689] |
| k_kxwtadoubles | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 146 | +0.0350 [+0.0095, +0.0601] | +0.0350 [+0.0095, +0.0601] |
| k_kxwtamatch | game_winner | kalshi_settlement_only | calibrated-frozen | all | 3456 | +0.0016 [+0.0008, +0.0024] | +0.0016 [+0.0008, +0.0024] |
| k_kxwtamatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 149 | +0.0604 [+0.0374, +0.0854] | +0.0604 [+0.0374, +0.0854] |
| laliga | game_winner | strict | frozen-market | phase2_sample | 277 | +0.0085 [-0.0010, +0.0173] | +0.0085 [-0.0010, +0.0173] |
| laliga | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0099 [-0.0035, +0.0228] | +0.0099 [-0.0035, +0.0228] |
| laliga | spread | strict | frozen-market | phase2_sample | 277 | +0.0081 [+0.0033, +0.0127] | +0.0090 [+0.0034, +0.0144] |
| laliga | team_total | exploratory | frozen-market | phase2_sample | 69 | +0.0168 [+0.0029, +0.0303] | +0.0168 [+0.0029, +0.0303] |
| laliga | total | strict | frozen-market | phase2_sample | 276 | +0.0077 [+0.0002, +0.0153] | +0.0071 [+0.0001, +0.0144] |
| ligamx | game_winner | strict | frozen-market | phase2_sample | 254 | +0.0090 [-0.0000, +0.0181] | +0.0090 [-0.0000, +0.0181] |
| ligamx | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0074 [-0.0044, +0.0195] | +0.0074 [-0.0044, +0.0195] |
| ligamx | spread | exploratory | frozen-market | phase2_sample | 201 | -0.0045 [-0.0106, +0.0011] | -0.0046 [-0.0106, +0.0010] |
| ligamx | team_total | exploratory | frozen-market | phase2_sample | 88 | +0.0028 [-0.0061, +0.0117] | +0.0028 [-0.0061, +0.0117] |
| ligamx | total | exploratory | frozen-market | phase2_sample | 201 | +0.0052 [-0.0013, +0.0118] | +0.0052 [-0.0013, +0.0118] |
| ligaportugal | game_winner | strict | frozen-market | phase2_sample | 226 | +0.0049 [-0.0050, +0.0143] | +0.0049 [-0.0050, +0.0143] |
| ligaportugal | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0030 [-0.0102, +0.0160] | +0.0030 [-0.0102, +0.0160] |
| ligaportugal | spread | exploratory | frozen-market | phase2_sample | 62 | +0.0051 [-0.0098, +0.0202] | +0.0062 [-0.0145, +0.0267] |
| ligaportugal | total | exploratory | frozen-market | phase2_sample | 62 | +0.0063 [-0.0075, +0.0203] | +0.0063 [-0.0075, +0.0203] |
| ligue1 | game_winner | strict | frozen-market | phase2_sample | 206 | +0.0083 [-0.0023, +0.0186] | +0.0083 [-0.0023, +0.0186] |
| ligue1 | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0077 [-0.0045, +0.0195] | +0.0077 [-0.0045, +0.0195] |
| ligue1 | spread | strict | frozen-market | phase2_sample | 206 | -0.0022 [-0.0077, +0.0033] | -0.0030 [-0.0088, +0.0028] |
| ligue1 | total | strict | frozen-market | phase2_sample | 206 | +0.0080 [+0.0010, +0.0152] | +0.0080 [+0.0008, +0.0150] |
| mlb | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0011 [-0.0058, +0.0077] | +0.0011 [-0.0058, +0.0077] |
| mlb | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0115 [+0.0023, +0.0205] | +0.0115 [+0.0023, +0.0205] |
| mlb | spread | strict | frozen-market | phase2_sample | 300 | +0.0027 [-0.0018, +0.0072] | +0.0027 [-0.0018, +0.0072] |
| mlb | team_total | exploratory | frozen-market | phase2_sample | 298 | +0.0094 [+0.0028, +0.0156] | +0.0094 [+0.0028, +0.0156] |
| mlb | total | strict | frozen-market | phase2_sample | 299 | +0.0082 [-0.0011, +0.0175] | +0.0080 [-0.0011, +0.0170] |
| mls | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0090 [+0.0014, +0.0165] | +0.0090 [+0.0014, +0.0165] |
| mls | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0064 [-0.0044, +0.0175] | +0.0064 [-0.0044, +0.0175] |
| mls | spread | exploratory | frozen-market | phase2_sample | 300 | +0.0048 [+0.0011, +0.0084] | +0.0049 [+0.0011, +0.0085] |
| mls | team_total | exploratory | frozen-market | phase2_sample | 186 | +0.0049 [-0.0006, +0.0102] | +0.0043 [-0.0017, +0.0099] |
| mls | total | exploratory | frozen-market | phase2_sample | 300 | +0.0035 [-0.0014, +0.0084] | +0.0035 [-0.0014, +0.0083] |
| nba | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0027 [-0.0073, +0.0120] | +0.0027 [-0.0073, +0.0120] |
| nba | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0125 [-0.0010, +0.0258] | +0.0125 [-0.0010, +0.0258] |
| nba | spread | strict | frozen-market | phase2_sample | 300 | +0.0102 [+0.0013, +0.0189] | +0.0107 [+0.0012, +0.0198] |
| nba | team_total | exploratory | frozen-market | phase2_sample | 288 | +0.0030 [-0.0073, +0.0134] | +0.0029 [-0.0074, +0.0132] |
| nba | total | strict | frozen-market | phase2_sample | 300 | +0.0113 [-0.0024, +0.0241] | +0.0113 [-0.0024, +0.0241] |
| ncaaf | game_winner | strict | calibrated-frozen | all | 656 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] |
| ncaaf | game_winner | strict | frozen-market | phase2_sample | 266 | +0.0605 [+0.0423, +0.0785] | +0.0605 [+0.0423, +0.0785] |
| ncaaf | game_winner | strict | blend-market | phase2_sample | 266 | +0.0060 [+0.0014, +0.0105] | +0.0060 [+0.0014, +0.0105] |
| ncaaf | game_winner | strict | blend-frozen | phase2_sample | 266 | -0.0545 [-0.0699, -0.0399] | -0.0545 [-0.0699, -0.0399] |
| ncaaf | game_winner | strict | frozen-market | phase1_v1_sample | 135 | +0.0547 [+0.0301, +0.0790] | +0.0547 [+0.0301, +0.0790] |
| ncaaf | game_winner | strict | blend-market | phase1_v1_sample | 135 | +0.0059 [-0.0007, +0.0122] | +0.0059 [-0.0007, +0.0122] |
| ncaaf | game_winner | strict | blend-frozen | phase1_v1_sample | 135 | -0.0488 [-0.0682, -0.0297] | -0.0488 [-0.0682, -0.0297] |
| ncaaf | spread | strict | calibrated-frozen | all | 655 | +0.0009 [-0.0004, +0.0023] | +0.0007 [-0.0006, +0.0020] |
| ncaaf | spread | strict | frozen-market | phase2_sample | 300 | +0.0805 [+0.0552, +0.1049] | +0.0864 [+0.0587, +0.1144] |
| ncaaf | spread | strict | blend-market | phase2_sample | 300 | +0.0004 [-0.0011, +0.0018] | +0.0007 [-0.0011, +0.0023] |
| ncaaf | spread | strict | blend-frozen | phase2_sample | 300 | -0.0801 [-0.1051, -0.0552] | -0.0858 [-0.1131, -0.0583] |
| ncaaf | team_total | exploratory | frozen-market | phase2_sample | 298 | +0.0711 [+0.0505, +0.0918] | +0.0710 [+0.0503, +0.0916] |
| ncaaf | total | strict | calibrated-frozen | all | 655 | +0.0010 [-0.0002, +0.0022] | +0.0010 [-0.0002, +0.0021] |
| ncaaf | total | strict | frozen-market | phase2_sample | 300 | +0.0187 [+0.0067, +0.0309] | +0.0187 [+0.0067, +0.0309] |
| ncaaf | total | strict | blend-market | phase2_sample | 300 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] |
| ncaaf | total | strict | blend-frozen | phase2_sample | 300 | -0.0187 [-0.0302, -0.0065] | -0.0187 [-0.0302, -0.0065] |
| ncaamb | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0190 [+0.0076, +0.0295] | +0.0190 [+0.0076, +0.0295] |
| ncaamb | game_winner | strict | frozen-market | phase1_v1_sample | 150 | -0.0062 [-0.0203, +0.0084] | -0.0062 [-0.0203, +0.0084] |
| ncaamb | spread | strict | frozen-market | phase2_sample | 297 | +0.0087 [-0.0007, +0.0181] | +0.0098 [-0.0008, +0.0207] |
| ncaamb | total | strict | frozen-market | phase2_sample | 300 | +0.0032 [-0.0085, +0.0154] | +0.0036 [-0.0086, +0.0160] |
| ncaawb | game_winner | strict | frozen-market | phase2_sample | 295 | +0.0111 [+0.0009, +0.0217] | +0.0111 [+0.0009, +0.0217] |
| ncaawb | game_winner | strict | frozen-market | phase1_v1_sample | 146 | -0.0029 [-0.0186, +0.0139] | -0.0029 [-0.0186, +0.0139] |
| nfl | game_winner | strict | frozen-market | phase2_sample | 89 | +0.0036 [-0.0160, +0.0241] | +0.0036 [-0.0160, +0.0241] |
| nfl | game_winner | strict | frozen-market | phase1_v1_sample | 89 | +0.0036 [-0.0162, +0.0224] | +0.0036 [-0.0162, +0.0224] |
| nfl | spread | strict | frozen-market | phase2_sample | 92 | +0.0118 [-0.0019, +0.0259] | +0.0118 [-0.0019, +0.0259] |
| nfl | team_total | strict | frozen-market | phase2_sample | 77 | +0.0117 [-0.0047, +0.0288] | +0.0117 [-0.0047, +0.0288] |
| nfl | total | strict | frozen-market | phase2_sample | 92 | +0.0259 [+0.0047, +0.0475] | +0.0265 [+0.0051, +0.0478] |
| nhl | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0021 [-0.0030, +0.0074] | +0.0021 [-0.0030, +0.0074] |
| nhl | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0029 [-0.0035, +0.0094] | +0.0029 [-0.0035, +0.0094] |
| nhl | spread | strict | frozen-market | phase2_sample | 300 | +0.0036 [-0.0006, +0.0078] | +0.0036 [-0.0006, +0.0078] |
| nhl | total | strict | frozen-market | phase2_sample | 300 | +0.0078 [+0.0022, +0.0137] | +0.0068 [+0.0010, +0.0127] |
| saudi | game_winner | strict | frozen-market | phase2_sample | 270 | +0.0134 [+0.0050, +0.0217] | +0.0134 [+0.0050, +0.0217] |
| saudi | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0164 [+0.0054, +0.0284] | +0.0164 [+0.0054, +0.0284] |
| saudi | spread | exploratory | frozen-market | phase2_sample | 188 | +0.0058 [-0.0017, +0.0139] | +0.0067 [-0.0018, +0.0155] |
| saudi | total | exploratory | frozen-market | phase2_sample | 188 | +0.0040 [-0.0046, +0.0130] | +0.0040 [-0.0046, +0.0130] |
| seriea | game_winner | strict | frozen-market | phase2_sample | 264 | +0.0061 [-0.0023, +0.0147] | +0.0061 [-0.0023, +0.0147] |
| seriea | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0058 [-0.0049, +0.0163] | +0.0058 [-0.0049, +0.0163] |
| seriea | spread | strict | frozen-market | phase2_sample | 262 | +0.0046 [+0.0001, +0.0091] | +0.0049 [+0.0001, +0.0098] |
| seriea | team_total | exploratory | frozen-market | phase2_sample | 50 | +0.0099 [-0.0062, +0.0263] | +0.0099 [-0.0062, +0.0263] |
| seriea | total | strict | frozen-market | phase2_sample | 261 | +0.0045 [-0.0027, +0.0120] | +0.0032 [-0.0046, +0.0107] |
| ucl | spread | strict | frozen-market | phase2_sample | 87 | +0.0046 [-0.0099, +0.0190] | +0.0055 [-0.0094, +0.0210] |
| ucl | total | strict | frozen-market | phase2_sample | 87 | +0.0206 [-0.0022, +0.0452] | +0.0206 [-0.0010, +0.0433] |
| uel | game_winner | strict | frozen-market | phase2_sample | 82 | +0.0242 [+0.0035, +0.0452] | +0.0242 [+0.0035, +0.0452] |
| uel | game_winner | strict | frozen-market | phase1_v1_sample | 82 | +0.0242 [+0.0032, +0.0462] | +0.0242 [+0.0032, +0.0462] |
| ufc | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0432 [+0.0259, +0.0601] | +0.0432 [+0.0259, +0.0601] |
| ufc | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0295 [+0.0038, +0.0536] | +0.0295 [+0.0038, +0.0536] |
| wnba | game_winner | strict | frozen-market | phase2_sample | 300 | +0.0101 [-0.0011, +0.0210] | +0.0101 [-0.0011, +0.0210] |
| wnba | game_winner | strict | frozen-market | phase1_v1_sample | 150 | +0.0116 [-0.0034, +0.0258] | +0.0116 [-0.0034, +0.0258] |
| wnba | spread | exploratory | frozen-market | phase2_sample | 300 | +0.0050 [-0.0042, +0.0150] | +0.0055 [-0.0041, +0.0157] |
| wnba | team_total | exploratory | frozen-market | phase2_sample | 100 | +0.0329 [+0.0136, +0.0528] | +0.0321 [+0.0129, +0.0523] |
| wnba | total | exploratory | frozen-market | phase2_sample | 300 | +0.0225 [+0.0105, +0.0338] | +0.0228 [+0.0107, +0.0342] |

## Limitations

- 2026 was examined in Phase 1; these are retrospective development results, not confirmation.
- Kalshi sports contracts start in 2025, so out-of-fold training/validation data exist only for competitions with 2025 markets; elsewhere calibration and blends are unavailable (thresholds were not lowered).
- Market references are sparse hourly candles (last candle ended by the cutoff, age <= 3 h); they are not executable prices.
- Contracts are sampled per event with fixed caps, only from contracts listed by the cutoff (listing time = later of created_time and open_time from cached market pages). The preserved Phase 1 sample was drawn without a listing check; its listed-after-cutoff contracts are counted above.
- Calibration and blends are fitted per competition x family; cohorts are reported separately but share one fitted map within a group.
- The frozen Kalshi-settlement-only training cap (150 events) equals the blend training minimum (150 matched events), so one missing quote makes a Kalshi-only blend unavailable. Neither the cap nor the threshold was changed after sampling.
- Event-equal and contract-weighted results coincide for game-winner market comparisons (one sampled contract per event); they can differ elsewhere.
- No closing lines, later quotes or reconstructed injury information are used.

## Prospective capture (manual only; no scheduler)

```powershell
.venv\Scripts\python.exe -m research.sports.phase2.run capture --mode checkpoint
.venv\Scripts\python.exe -m research.sports.phase2.run capture --mode early
.venv\Scripts\python.exe -m research.sports.phase2.run capture-replay --record <record.json>
```

Checkpoint window: [cutoff - 20 min, cutoff], cutoff = start - 60 min = evidence cutoff. Captures outside the window are not recorded; captures after the cutoff, or with evidence retrieved after it, are MISSED; nothing is backfilled. Early snapshots are development records labelled with their actual horizon.

## Reproduction

```powershell
.venv\Scripts\python.exe -m research.sports.phase2.run reproduce   # cache-only: oof, sample, select, evaluate, report
.venv\Scripts\python.exe -m research.sports.phase2.run verify --protocol sports_phase2_v1
.venv\Scripts\python.exe -m research.sports.run verify --protocol sports_phase1_v2
```
