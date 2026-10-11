# Sports capture v2: manual pilot collection log

RESEARCH_ONLY / NO_BET. Manual sessions only; there is no scheduler. All models, fitted maps, protocols (`sports_phase2_capture_v2`, `sports_capture_v2_scoring_v1`) and thresholds are frozen. Each session captures every supported competition inside its window at run time, with no selection by prediction, price or past performance. Results are availability-based and descriptive. Comparisons are paired on common contracts with unique event counts; standalone candidate averages over different samples are not compared. Raw records and evidence stay local.

Session procedure (repo root, `.venv\Scripts\python.exe -m`):

```text
research.sports.live.run verify
research.sports.live.run upcoming --hours 4
research.sports.live.run capture --mode window
research.sports.live.run replay --record <each newly accepted record>
research.sports.scoring.run settle
research.sports.scoring.run verify-scores
research.sports.scoring.run report
```

Earlier runs (2026-10-10: two all-competition runs and one NCAAF run) are described in `sports_capture_v2_live_check.md` and `sports_capture_v2_ncaaf_scoring_check.md`.

## Session 2026-10-11 S1 (12:24-12:31Z; 08:24-08:31 Indianapolis)

| time (UTC) | command | result |
|---|---|---|
| 12:25:06 | `live.run verify` | OK (313 competitions) |
| 12:25:17 | `live.run upcoming --hours 4` | windows open now and from 12:40Z on |
| 12:25:24 | `live.run capture --mode window` (run 20261011T122524791039Z) | 7,820 milestones, 332 events in 66 competitions; WINDOW_CAPTURE 11, MISSED 39, FAILED_ATTEMPT 0, problems 0; started_not_recorded 45, window_not_open 17, no_linked_contracts 57, unsupported_adapter 1 (F1) |
| 12:29:30 | `live.run replay --record` (50 new accepted records) | 50 OK (11 captures, 39 MISSED) |
| 12:30:08 | `scoring.run settle` | 0 newly finalized; 978 already finalized (byte-identical); 329 PENDING |
| 12:30:47 | `scoring.run verify-scores` | 978 OK, 0 refused |
| 12:30:48 | `scoring.run report` | pilot CSV unchanged |

- **MISSED:** the 39 MISSED records are events whose window closed before this session started. They were not backfilled.

**New captures (11 events):**
- Tennis 6: ATP challenger 3, ITF 2, WTA challenger 1.
- Soccer 2: Bundesliga, Saudi Pro League.
- NFL 1 (London game).
- Darts 1.
- T20 cricket 1.

Horizons were 61.1-73.4 min.

Candidate availability in new captures (contracts with a probability; status-ok contracts):

| sport | family | contracts | frozen | calibrated | market | blend |
|---|---|---|---|---|---|---|
| Football (NFL) | game_winner | 2 | 2 | 0 | 1 | 0 |
| Football (NFL) | spread | 25 | 25 | 0 | 2 | 0 |
| Football (NFL) | total | 19 | 19 | 0 | 1 | 0 |
| Football (NFL) | team_total | 27 | 27 | 0 | 2 | 0 |
| Football (NFL) | segment | 178 | 178 | 0 | 0 | 0 |
| Soccer | game_winner | 6 | 6 | 0 | 2 | 0 |
| Soccer | spread / total / team_total | 8 / 12 / 6 | all | 0 | 4 / 4 / 2 | 0 |
| Soccer | segment / score_props | 26 / 2 | all | 0 | 0 | 0 |
| Tennis | game_winner | 12 | 12 | 0 | 6 | 0 |
| Darts | game_winner | 2 | 2 | 0 | 0 | 0 |
| Cricket | game_winner | 2 | 2 | 0 | 1 | 0 |

Calibrated and blend are unavailable for every new capture: these groups have no fitted map (only NCAAF has blend maps). The darts market was unavailable for this event under the frozen candle rule.

**Pending settlements (329 contracts in 12 events):**
- NFL 251, Bundesliga 37, Saudi 23;
- ATP challenger 6, ITF 4;
- darts 2, cricket 2, WTA challenger 2;
- 2 from an earlier Liiga game whose market is still `active`.

**Cumulative paired comparisons** (978 labelled contracts in 43 events; mean loss difference a - b on common contracts; contract-weighted, with event-equal in parentheses):

| pair | group | contracts / events | d Brier | d log loss |
|---|---|---|---|---|
| frozen - market | all | 156 / 43 | +0.0082 (+0.0123) | +0.0197 (+0.0282) |
| frozen - market | game_winner | 36 / 36 | +0.0239 | +0.0569 |
| frozen - market | spread | 54 / 27 | -0.0045 | -0.0074 |
| frozen - market | total | 44 / 27 | +0.0116 (+0.0166) | +0.0247 (+0.0346) |
| frozen - market | team_total | 22 / 11 | +0.0071 | +0.0152 |
| calibrated - frozen | NCAAF only | 313 / 10 | -0.0026 (-0.0026) | -0.0067 (-0.0062) |
| blend - frozen | NCAAF only | 40 / 10 | -0.0072 | -0.0105 |
| blend - market | NCAAF only | 40 / 10 | +0.0002 | +0.0007 |

These are descriptive point estimates from a small, availability-based sample. Many contracts share an event, so the event counts are the relevant sample size.

**Next windows (opening times in Indianapolis, EDT, 2026-10-11; from `upcoming` at 12:25Z):**
- **08:40:** 26 Kalshi events across 17 competitions, including ATP challenger, ITF women, KHL, Greek, Danish, Belgian and Egyptian soccer, and VTB basketball.
- **08:45, 09:00:** ATP challenger.
- **08:55:** La Liga and La Liga 2.
- **09:10:** Liga Portugal, Allsvenskan, Swiss league, DEL hockey (4), ATP challenger.
- **09:25:** Eredivisie.
- **09:40:** ACB basketball, ELH hockey (4), Eliteserien, Italian basketball and others.

These are milestone windows; whether contracts link is determined at capture time. No session was scheduled or backfilled.
