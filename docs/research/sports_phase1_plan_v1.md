# Sports research track - Phase 1 implementation plan (v1)

Status: RESEARCH_ONLY / NO_BET. No paid services, no orders, no scheduled collectors registered.
Companion documents: [`sports_feasibility_v1.md`](sports_feasibility_v1.md) (audit, coverage matrix,
readiness classes) and [`sports_inventory_v1.csv`](sports_inventory_v1.csv) (one row per Kalshi
sports series).

Weather Phase 7 (collector, scheduling, ownership, credentials, frozen protocol, calibration pools,
historical artifacts) is out of scope for every item below. Sports code lives in `research/sports/`
and must not import from `research/weather/` at runtime.

## 1. Principles

1. **One pipeline, many adapters.** Every sport and contract family flows through the same stages;
   sport- and contract-specific logic lives only in source adapters, contract adapters, and model
   engines.
2. **Time is explicit.** Every fact carries when it happened (`event_time`), when it became public
   (`available_at`), and when we fetched it (`retrieved_at`). Features are computed only from facts
   with `available_at <= as_of`.
3. **Final results are not pre-event inputs.** A result table proves only what happened. Anything
   used before an event (lineups, injuries, starters, odds) needs its own dated source or must be
   captured prospectively.
4. **Forecast quality and tradable edge are separate deliverables.** Phase 1 measures forecast
   quality. Edge claims require contemporaneous executable prices, fees, liquidity, and latency, and
   are deferred.
5. **Correlated contracts are evaluated as groups.** Ladders, mutually exclusive outcomes, and
   same-game props are scored with the event (or tournament) as the unit of independence.
6. **Weather isolation.** No edits to `research/weather/`, weather scripts, scheduler tasks, collector
   config, calibration CSVs, or Phase 7 artifacts. Shared modules (`kalshi/*`, `research/prices.py`,
   `research/settlement.py`, `research/efficiency.py`, `research/sample_identity.py`) are imported
   read-only; if one needs a change, sports gets a wrapper or a copy instead.

## 2. Shared architecture

### 2.1 Stages

```text
source adapters ──► raw cache ──► normalizers ──► normalized store
 (Kalshi, ESPN,     (immutable,    (per source)    (competitions, seasons, participants,
  league APIs,       hashed bytes,                  events, results, markets, prices,
  bulk files)        provenance)                    crosswalks)
                                                        │
                                                        ▼
       reports ◄── chronological ◄── outcomes ◄── predictions ◄── as-of features
     (per family,    evaluation     (Kalshi result   (write-once     (feature store keyed by
      per sport)     (rolling-      + independent    journal, model   participant/event and as_of;
                     origin, CIs)   reconciliation)  + version hash)  availability-gated)
                                                        ▲
                                     contract adapters ─┘ (map a market's payoff onto the
                                                          model's outcome distribution)
```

### 2.2 Package and data layout

| Path | Contents | Tracked in git |
|---|---|---|
| `research/sports/kalshi_discovery.py` | Inventory crawler (exists, from this audit) | yes |
| `research/sports/families.py` | Sport / contract-family classifier (exists) | yes |
| `research/sports/source_registry.py` | Source assessments (exists) | yes |
| `research/sports/adapters/<source>.py` | One module per external source: fetch + parse | yes |
| `research/sports/normalize/` | Entity schemas, ID minting, crosswalks | yes |
| `research/sports/features/` | As-of feature builders (ratings, rest, form, standings) | yes |
| `research/sports/engines/` | Model engines (pairwise rating, score distribution, field simulation, season simulation, player rate) | yes |
| `research/sports/contracts/` | Contract adapters per family (payoff over outcome space) | yes |
| `research/sports/evaluate/` | Metrics, chronological splits, clustered bootstrap, reports | yes |
| `data/cache/sports/raw/<source>/...` | Raw bytes + `.meta.json` provenance sidecars | no (`data/cache/` ignored) |
| `data/sports/normalized/*.jsonl` | Normalized entity tables (versioned by schema) | no (add to `.gitignore` in Batch 0) |
| `data/sports/predictions/<family>/<yyyy>/<id>.json` | Write-once prediction records | no |
| `data/results/sports_*` | Reports and summaries | no (`data/results/` ignored) |
| `research/sports/protocols/*.json` | Frozen evaluation protocols (hashes, splits, metrics) | yes |

Dependencies: the shared `requirements-lock.txt` is also the weather collector's lock, so sports
must not add packages to it. Batch 0 creates `requirements-sports.txt` (and a separate venv) if
numerical libraries are needed; engines should stay stdlib-first (`math`, `statistics`, `random`
with seeded `Random`) so the first baselines run in the existing environment.

### 2.3 Source adapters

Each adapter implements the same small contract:

```python
class SourceAdapter(Protocol):
    source_id: str                      # key in source_registry.SOURCES
    def plan(self, scope: Scope) -> list[Request]: ...       # deterministic request list
    def fetch(self, req: Request) -> RawRecord: ...          # bytes + provenance, no parsing
    def parse(self, raw: RawRecord) -> list[NormalizedFact]: ...  # pure, versioned parser
```

- `fetch` writes raw bytes first (atomic, via `kalshi.cache.atomic_write_text` or a bytes
  equivalent), then a sidecar: `source_id, url, params, http_status, retrieved_at, sha256,
  bytes, parser_version, adapter_version, rate_limit_state`.
- `parse` is pure and replayable from raw bytes, so parser fixes never require refetching.
- Rate limiting and retries reuse the pattern in `kalshi.client.KalshiClient` (min delay, backoff
  on 429/5xx, no retry on 404). Per-source budgets come from `source_registry` (for example
  Sports Reference 20 req/min, Liquipedia LPDB 60 req/hour).
- Planned adapters, in priority order: `kalshi_markets` (series/events/markets/historical, from
  the discovery crawler), `kalshi_prices` (candlesticks/trades), `kalshi_milestones` (+ structured
  targets), `kalshi_live_data` (final state, game stats), `espn_site_api`, `nflverse`,
  `nhl_api`, `mlb_statsapi`, `football_data_co_uk`, `jolpica_f1`/`openf1`, `cricsheet`,
  `liquipedia`/`opendota`, `ncaa_casablanca`, `squiggle`.

### 2.4 Normalized entities and stable identifiers

| Entity | Stable ID | Construction | Crosswalk keys kept |
|---|---|---|---|
| Sport | `sport` | Closed enum (`basketball`, `football`, ...) | Kalshi tag, `filters_by_sport` key |
| Competition | `comp:<sport>.<slug>` | Hand-curated slug (`comp:basketball.nba`) | Kalshi `product_metadata.competition`, ESPN league path, league codes |
| Season | `season:<comp>.<label>` | League's own label (`2025-26`, `2026`) | source season IDs |
| Participant | `pt:<uuid>` | Kalshi structured-target UUID when one exists, else UUIDv5 over (`sport`, source, source id) | Kalshi `structured_target_id`, `source_ids`, ESPN/league IDs, name history with validity dates |
| Event | `ev:<sha1[:16]>` | Hash of (competition, season, home/first participant, away/second participant, **original** scheduled local date, game number) | Kalshi `event_ticker`(s), `milestone_id`, ESPN event id, league game id |
| Market | Kalshi `ticker` | Already stable | `event_ticker`, `series_ticker`, contract-terms URL + SHA-256 of the PDF |
| Contract spec | `cs:<sha1[:16]>` | Hash of parsed payoff spec (family, subject, stat, period scope, comparator, strike(s), OT/shootout flag, void rules version) | market ticker |

Rules:

- The event ID uses the **originally scheduled** local date because Kalshi rules reference "the
  game originally scheduled for <date>" and postponements keep the same contract.
- Doubleheaders and series games carry `game_number`; tournaments use a `round`/`leg` field.
- Participant names are never keys. Renames and relocations are rows in a validity-dated alias
  table, matching Kalshi's "tracked through official name changes" rule language.
- Kalshi milestones already link `primary_event_tickers` to `home_team_id`/`away_team_id`
  (structured targets) and to external `source_*_id` values; they are the preferred crosswalk seed
  for 2025+ events.

### 2.5 Time and timezones

- Store every timestamp as UTC ISO-8601 with offset. Keep the venue's IANA zone on the event
  (`tzdata` is already pinned) to derive the local game date.
- Kalshi tickers encode dates and times in US Eastern (for example `KXMLBGAME-26OCT152000MILLAD`);
  parse them with `America/New_York`, never with the host's local zone.
- League "game dates" are local dates; season-day boundaries (for rest-day features) use the
  local date of the venue.
- Each fact carries:
  - `event_time`: when it happened (tip-off, final whistle).
  - `available_at`: when it is treated as public. For backfilled results, use the official end
    time plus a conservative lag; for dated publications, use the publication timestamp; for
    prospective captures, use `retrieved_at`.
  - `retrieved_at`: when we fetched it.
  - `first_seen_at`: earliest `retrieved_at` across captures (for prospective sources).
- These follow the convention already used in `research/weather/evidence.py`
  (`issued_at` / `available_at` / `retrieved_at`), re-declared in sports code rather than imported.

### 2.6 As-of feature store

- Feature rows are keyed by (`entity_id`, `as_of`) and record `inputs_max_available_at`, the
  maximum `available_at` of every fact used. Evaluation rejects rows where
  `inputs_max_available_at > as_of`.
- Ratings (Elo, Poisson strengths, player Elo, golf scoring averages) are computed by a single
  forward pass over results sorted by `available_at`, emitting a snapshot after each update. That
  makes historical as-of features exact by construction.
- Inputs that exist only as current snapshots (Kalshi milestone `game_injuries`, `last_games`,
  lineups, probable starters) are flagged `asof=none` in the registry and may only enter models
  from prospective captures.

### 2.7 Contract adapters (separate per contract type)

A contract adapter converts a market into a payoff function over a model's outcome space and
declares which outcomes are void or fair-price settled:

| Adapter | Families | Outcome space consumed | Key rule parameters |
|---|---|---|---|
| `binary_winner` | game_winner, segment winner, series winner | P(participant wins scope) (+ draw/tie) | tie handling (Tie strike vs 50/50), OT inclusion, regulation-only scopes |
| `margin_ladder` | spread, segment spread, winning margin | margin distribution | strike, comparator, OT inclusion, tie = zero differential |
| `count_ladder` | total, team_total, segment total, corners, maps/sets | count distribution | strike, comparator, OT/shootout/extra-time inclusion, penalties excluded |
| `score_event` | BTTS, correct score, first to score, HT/FT, method/round of finish | joint score / event-sequence distribution | own goals, extra time, KO vs TKO definitions |
| `player_stat` | player props, H2H player props | player stat distribution | DNP rule (No vs fair price), entered-game condition, period scope |
| `field_position` | golf/motorsport/Olympics finish, top-N, H2H matchups | finishing-order simulation | tie counts as position, withdrawal before/after start, shortened events |
| `progression` | tournament outright, to advance, season wins, playoff seeds | season/bracket simulation | co-champion split, early resolution, cancellation split |
| `stat_leader` | league leaders, series leaders | season stat simulation | ties split 1/N, qualification rules |

Settlements to an exchange-determined "fair price" (cancellations, walkovers, non-entry) are
**not** binary outcomes. `research/settlement.validate_binary_result` already refuses non-binary
results; sports evaluation must report these separately and exclude them from proper-score
averages.

### 2.8 Predictions, outcomes, reports

- **Predictions**: write-once JSON records keyed by (`market or event`, `as_of`, `model_id`),
  following the immutable-snapshot pattern of `research/weather/snapshots.py` (conflict on
  re-write with different content). Each record stores the model version hash, feature-row hashes,
  `inputs_max_available_at`, full outcome distribution, and per-market probabilities.
- **Outcomes**: primary label is Kalshi's `result` for the market; secondary is the independent
  source result (ESPN/league/Kalshi live data). Disagreements are logged, never silently
  overwritten. Fair-price and voided markets are kept with their settlement value but marked
  `non_binary`.
- **Reports**: per family x sport tables of metrics with clustered intervals, reliability
  diagrams (as CSV bins), market-benchmark comparisons, and sample-size labels reused from
  `research/efficiency.sample_size_label`.

### 2.9 Reuse map (read-only imports, no modifications)

| Module | Reuse for |
|---|---|
| `kalshi/client.py` | All Kalshi reads (pagination, retry, 404 handling, historical routing) |
| `kalshi/cache.py` | Atomic writes, JSON/JSONL helpers |
| `kalshi/fees.py`, `kalshi/fee_index.py` | Time-aware fee schedules (quadratic and maker-fee types found in sports series) for later edge work |
| `research/prices.py` | Candle normalization and no-lookahead candle selection for market benchmarks |
| `research/settlement.py` | Binary-result validation; payout arithmetic for later edge work |
| `research/efficiency.py` | `wilson_interval`, event-clustered `bootstrap_roi_ci` (pattern for clustered bootstrap) |
| `research/sample_identity.py` | Frozen sample hashing for evaluation sets |
| `research/weather/evidence.py`, `snapshots.py` | **Pattern only** (timestamp semantics, write-once journal); re-implemented under `research/sports/` |

## 3. Baselines per contract family

The goal is the simplest defensible baseline per family, not a strong model. "Shared engine"
means one implementation with per-sport configuration.

| Family | Simplest defensible baseline | Engine (shared?) | Inputs (as-of) | Why not Elo-only |
|---|---|---|---|---|
| game_winner (two-way team sports, tennis, esports, MMA, darts, table tennis, volleyball, lacrosse, cricket, AFL, rugby) | (1) home/base-rate prior; (2) margin-aware Elo with home advantage and between-season regression | **Pairwise rating engine** (shared; per-sport K, home edge, MOV multiplier, surface/map variants) | Prior results only | Elo is appropriate here |
| game_winner (soccer three-way, and any sport with a Tie strike) | Rating difference -> independent Poisson goals (Dixon-Coles-lite) giving H/D/A | **Score-distribution engine** | Prior results (goals) | Draw probability needs a score model |
| spread, segment spread, winning margin | Margin ~ Normal(rating diff, sport sigma) for high-scoring sports; Skellam from Poisson for soccer/hockey; NegBin runs for baseball | Score-distribution engine | Prior results with scores | Elo gives P(win), not a margin distribution |
| total, team_total, segment total, corners, maps/sets totals | Team offense/defense (pace) strengths fit on rolling window -> count/Normal total distribution; segment = full game scaled by empirical segment share | Score-distribution engine | Prior results with line scores | Totals depend on scoring rates, not relative strength |
| score_props (BTTS, correct score, first to score, HT/FT, overtime/extra innings, YRFI) | Derived from the same joint score distribution (Poisson / NegBin with empirical first-segment shares) | Score-distribution engine | Prior results (+ PBP for timing props) | Joint outcomes |
| score_props (MMA/boxing method or round of finish) | Empirical base rates by weight class and fighter finish rates, shrunk to class mean | **Categorical base-rate engine** (distinct) | Prior fight results | Not a strength question |
| player_prop (single game) | Player rolling per-opportunity rate x expected opportunities (minutes/PA/targets/shots) with empirical-Bayes shrinkage to position mean; Poisson/NegBin for counts, Normal for yards | **Player-rate engine** (distinct; shared across sports) | Prior box scores; conditional on player appearing | Props are about one player's volume |
| field_finish (golf, motorsport, Olympics, H2H matchups in fields) | Rolling performance rating (golf: score relative to field, shrunk; racing: recent finishes + grid) -> Plackett-Luce / Normal-performance Monte Carlo for positions, top-N, H2H | **Field-simulation engine** (distinct; shared across field sports) | Prior results; grid/qualifying (public pre-race) | Many-competitor ordering |
| tournament_outright, to advance, series_playoff | Monte Carlo of remaining bracket/schedule using the game-level engine for each matchup | **Season/bracket simulator** (shared; consumes game engines) | As-of standings, bracket, remaining schedule | Futures are compositions of games |
| season_wins, playoff seeds, streaks | Same simulator over the remaining schedule | Season/bracket simulator | As-of standings | Same |
| league_leaders, series stat leaders | As-of season totals + player-rate engine projected over remaining games, simulated jointly | Player-rate engine + simulator | As-of season stats | Leader identity is an extreme-value question |
| awards, draft, rankings_polls | Base-rate / stat-leader heuristic only; documented as weak baseline; market price is the benchmark | none (heuristics) | As-of stats | Voting/selection processes; no free as-of odds |
| personnel_ops, other_novelty, combo_parlay | Out of scope for Phase 1 | - | - | - |

Shared implementations: the **pairwise rating engine** serves every two-competitor win market;
the **score-distribution engine** serves soccer three-way winners, spreads, totals, team totals,
segments, and score props for each team sport; the **season/bracket simulator** serves every
futures-style family by calling the game-level engines. Distinct implementations are required for
**player props** (per-player rates and availability), **field events** (many-competitor ordering),
and **categorical fight outcomes**. Elo is not used for totals, props, or futures directly.

## 4. Evaluation protocol

### 4.1 Forecast-quality metrics

- **Binary markets**: Brier score and log loss (probabilities clipped to [0.001, 0.999] and the
  clipping rate reported), plus reliability bins and expected calibration error.
- **Mutually exclusive outcome sets** (H/D/A, finishing position, champion): multiclass log loss
  and ranked probability score for ordered outcomes.
- **Ladders** (spreads, totals, player-stat thresholds): score the event's full predictive
  distribution once (ranked probability score over strikes, or CRPS for the underlying count),
  instead of averaging many correlated binary Brier scores. Binary scores are reported as a
  secondary, event-weighted view.
- **Skill vs benchmarks**: Brier/log-loss skill relative to (a) the naive base rate and (b) the
  Kalshi market-implied probability at the same `as_of` (mid of the selected candle's bid/ask
  close via `research/prices.select_candle_for_horizon`, with staleness limits). The market is a
  benchmark, not training data.

### 4.2 Chronological design

- Rolling-origin evaluation: fit/update on data with `available_at <= as_of`, predict at fixed
  horizons before event start (for example T-24h, T-2h, T-15m), step forward by date.
- Hyperparameters (K-factors, shrinkage, segment shares) are chosen on a training period and then
  frozen in a protocol file before the test period is scored (same discipline as the weather
  Phase 7 frozen protocol).
- Test periods are contiguous calendar blocks, not random splits; seasons are never split across
  train/test boundaries for season-level families.

### 4.3 Dependence-aware uncertainty

- Unit of resampling: the **event** for game-level families (all markets of a game, including all
  ladder strikes and same-game props, move together); the **tournament or season** for futures,
  outrights, and season families; the **slate/day** as a second-level cluster for sports with
  many simultaneous games (shared shocks such as officiating or weather).
- Intervals: cluster (block) bootstrap of skill differences (model minus benchmark), paired by
  event, with >= 2,000 resamples and a fixed seed; report the effective number of clusters.
  `research/efficiency.bootstrap_roi_ci(by_event=True)` is the existing pattern.
- Multiple comparisons: report all sport x family cells; label results by sample size; do not
  promote a cell on a single favorable interval.

### 4.4 Forecasting quality vs tradable edge

Phase 1 stops at forecast quality. A later edge study would additionally need, per prediction:
contemporaneous executable price at the decision time (ask for buys), resolved fee schedule at
entry (`kalshi.fees.resolve_fee_schedule`), available size at that price, latency between
`available_at` of inputs and the order, and fair-price/void settlements. Kalshi's API provides
candles (OHLC of bid/ask/trade) and trade prints, but **no historical order-book depth**, so
executable size can only be bounded historically and must be measured prospectively.

## 5. Batches

Readiness classes and counts come from the coverage matrix in
[`sports_feasibility_v1.md`](sports_feasibility_v1.md) section 3 (178 sport x family cells:
43 A, 27 B, 47 C, 1 D, 38 E, 22 F). Volumes are Kalshi `volume_fp` contracts summed over all
markets in the cell; "events" is the estimated number of settled Kalshi events.

Priority score used to order work inside a batch (explicit, so it can be re-run): high Kalshi
volume, many settled events (statistical power), a verified free results source that is
independent of Kalshi, an available external benchmark (closing odds), and reuse of an engine
already built in an earlier step. Lower priority for rate-limited or HTML-only sources.

### Batch 0 - Shared infrastructure (prerequisite for everything)

No modelling. Deliverables:

1. `.gitignore`: add `data/sports/` (normalized store, predictions). The audit's raw API docs
   already live in the ignored `data/cache/sports/raw/kalshi_docs/`.
2. `research/sports/adapters/base.py`: adapter protocol (`plan`, `fetch`, `parse`), raw cache
   writer with sha256 + provenance sidecar, polite rate limiter, retry policy that records
   exhausted retries as incomplete (the audit showed 66 of 11,814 paginations exhausted five 429
   retries on the first pass).
3. `adapters/kalshi.py`: events, markets (both tiers, routed by the historical cutoff), candles,
   trades; parses rules, `settlement_sources`, early-close conditions, and results including
   void/fair-price settlements.
4. `normalize/`: competition, season, participant, event, result, market entities; ID minting
   (section 2.4) and crosswalk tables Kalshi <-> ESPN <-> league IDs, keyed on structured-target
   UUIDs where Kalshi provides them.
5. `features/asof.py`: availability-gated feature store; refuses facts with `available_at > as_of`.
6. `predictions/journal.py`: write-once prediction records with model and protocol hashes.
7. `evaluate/`: metrics (section 4.1), rolling-origin driver, event/tournament cluster bootstrap,
   Kalshi benchmark lookup via `research/prices.py`, report writer.
8. Tests: ID stability across postponements, timezone conversions (Kalshi tickers in
   America/New_York, venue local dates), availability gating, void exclusion, fee resolution.

Gate: replay one week of Kalshi NBA game markets end to end with a constant 0.5 forecast; reports
show correct counts of events/markets, void exclusions, and benchmark coverage.

### Batch 1 - Categories ready for historical evaluation (class A)

Ordered by priority; each step reuses the engines of the steps before it.

| Step | Sport / families | Kalshi events / volume | Results source (free) | Benchmark | Why this position |
|---|---|---|---|---|---|
| 1.1 | Basketball (NBA, WNBA, NCAA men's/women's): game_winner, spread, total, team_total, segment, score_props | 16,055 winner events; 20.4B contracts on winners, 2.8B spreads, 2.4B totals | ESPN site API (scores, line scores, PBP); Basketball-Reference for spot checks only | Kalshi prices only (no free historical odds) | Largest A-class volume; builds both the pairwise rating engine and the Normal score-distribution engine |
| 1.2 | Football (NFL, NCAAF): game_winner, spread, total, team_total, segment, score_props | 2,235 winner events; 12.8B winners, 2.1B spreads, 1.2B totals | nflverse `games.csv` (1999-2026, CC-BY-4.0 data release); ESPN for NCAAF (CFBD needs a free key, not exercised) | nflverse closing spread/total/moneyline | Second engine reuse; the only US sport with a free closing-line benchmark for a pre-Kalshi skill check |
| 1.3 | Soccer: three-way winner, spread, total, team_total, segment, BTTS/correct score | 17,248 winner events; 14.8B winners, 2.0B totals, 1.9B score props | football-data.co.uk (with Pinnacle closing odds), openfootball (CC0), ESPN | football-data.co.uk closing 1X2/OU2.5/AH | Builds the Poisson score engine; strong free benchmark. Start with leagues covered by football-data.co.uk; Kalshi lists far more competitions than free sources cover |
| 1.4 | Baseball (MLB first; NPB/KBO later): game_winner, total, spread, team_total, segment (F5/innings), YRFI | 14,757 winner events; 11.3B winners, 2.7B totals | Retrosheet (bulk, attribution) + MLB Stats API (non-bulk reads) | Kalshi prices only | Negative-binomial runs engine; probable pitchers are not as-of, so the baseline is team-level only |
| 1.5 | Hockey (NHL): game_winner, total, spread, segment, score_props | 4,363 winner events; 1.7B winners | NHL api-web (2010+ verified) | Kalshi prices only | Reuses Poisson engine; shootout rule check required for totals |
| 1.6 | Golf field_finish; Motorsport field_finish (F1 first) | Golf 3,939 events (4.8B); motorsport 602 (290M) | ESPN golf leaderboards, OWGR, PGA Tour pages; Jolpica (1990+) and OpenF1 | Kalshi prices only | Builds the field-simulation engine. Kalshi "events" here are market groups per tournament, so independent tournaments number in the tens per season |
| 1.7 | MMA game_winner + method/round score_props | 762 fights; 1.4B winners, 360M props | ESPN UFC endpoints, Kalshi live data | Kalshi prices only | Pairwise engine reuse plus categorical base-rate engine; UFCStats blocked, so fight-level stats are thin |
| 1.8 | Cricket game_winner | 2,578 matches; 2.5B | Cricsheet (ODC-By/CC-BY) | Kalshi prices only | Format split (T20/ODI/Test) and DLS rules; good free data, moderate volume |
| 1.9 | Esports game_winner, maps total, map winner | 15,589 matches; 1.3B winners | OpenDota (Dota 2); Liquipedia API (60 req/hour, approval) for others; HLTV pages | Kalshi prices only | High event count but source access is the bottleneck for CS2/LoL/Valorant |
| 1.10 | Aussie Rules, lacrosse, volleyball game_winner | 224 / 501 / 800 events; 7.7M / 3.9M / 1.7M | Squiggle + AFL Tables; ESPN | Squiggle dated model tips (AFL) | Low volume; AFL is cheap because Squiggle gives a dated external forecast benchmark |

Gate per step: frozen protocol file committed before the test period is scored; report shows
Brier/log loss (and RPS/CRPS for ladders), reliability, skill vs naive base rate and vs Kalshi
benchmark with event-clustered intervals, void/fair-price exclusions counted, and a
reconciliation rate between Kalshi results and the independent source of at least 99% (every
mismatch listed).

### Batch 2 - Limited historical evaluation (class B)

| Item | Cells | Limitation | Plan |
|---|---|---|---|
| Tennis (highest-volume sport: 29.2B contracts on 66,535 match events) | game_winner, segment, total, spread, score_props | No verified free bulk results: Sackmann repos absent at their GitHub location, tennis-data.co.uk blocked from this host, ESPN covers main tours only; most Kalshi volume includes Challenger/ITF matches | First task: measure ESPN ATP/WTA coverage against Kalshi tennis events; retry tennis-data.co.uk from another network; search for relocated Sackmann data. If coverage of Kalshi events is at least 80%, promote main-tour matches to A and run the pairwise engine with surface variants. Kalshi live data may label outcomes but cannot serve as the independent check |
| Player props (MLB 18,367 events, NBA 8,727, NFL 3,955, NHL 3,157, soccer 1,356) | player_prop | Lineups, minutes, injuries, starting pitchers are overwritten; DNP/void rules vary by template | Results-only player-rate baseline conditional on the player appearing; report it as an upper bound on information and never as a deployable forecast. Real evaluation moves to Batch 3 capture |
| Tournament outrights with many events (soccer 2,849; basketball 305) | tournament_outright | Outcomes heavily correlated within a tournament | After Batch 1 engines exist, run the bracket simulator; cluster on tournament |
| Table tennis (15,209 events), darts, rugby, squash | game_winner | Results source partial, HTML only, or Kalshi-only | Pairwise engine on Kalshi-settled outcomes, labelled "Kalshi-only labels"; low volume (under 20M contracts each) |
| Olympics, cycling, small field cells, cricket/hockey team totals | field_finish, team_total, score_props | Few settled events | Run only as a by-product of shared engines; no standalone claims |

### Batch 3 - Categories requiring prospective collection (class C, plus inputs for A/B)

Design only in Phase 1. **No collector is registered and no scheduled task is created.** A future
capture job needs separate approval, must run under its own task name and lock, and must not
share scheduling, credentials, or files with the weather Phase 7 collector.

- **Input capture for A/B families:** injury reports, confirmed lineups, probable/starting
  pitchers and goalies, weather for outdoor venues, and Kalshi milestone `game_injuries` snapshots,
  each stored with `retrieved_at` so later evaluation is genuinely as-of.
- **Price capture for edge studies:** periodic order-book snapshots (top of book and depth) for
  the A-class series, because historical depth does not exist.
- **Season-level families:** awards (14 cells), league leaders (8), season wins (5 C), draft (2),
  rankings/polls (3), and small tournament outrights (15 C): one outcome per team- or
  player-season, so even with capture the sample grows by roughly one observation per entity per
  year. Treat Kalshi price as the benchmark and report results only after multiple seasons.
- **Boxing and chess:** too few settled events and blocked or partial sources (BoxRec blocked;
  FIDE/Lichess partial); collect results prospectively alongside prices.

### Batch 4 - Blocked or out of scope (classes D, E, F)

- **D (1 cell):** KenPom rankings markets settle on paid KenPom ratings. Revisit only if a paid
  source is approved (not in this track's constraints).
- **E (38 cells):** personnel moves, novelties, combos/pre-packs, test series, and sports-tagged
  non-sport series. No defensible statistical design; combos are derived from leg markets.
- **F (22 cells):** no traded markets and nothing open or upcoming. Re-check on the next inventory
  crawl.

## 6. Priorities and reasons (summary)

1. **Batch 0 first**, because every later result depends on correct IDs, availability gating,
   void handling, and clustered evaluation.
2. **Basketball, football, soccer** next: highest A-class volume, and two of them (NFL via
   nflverse, European soccer via football-data.co.uk) have free closing-line benchmarks that test
   the pipeline against a strong external forecast before comparing with Kalshi.
3. **Baseball and hockey** follow as engine reuse with large samples.
4. **Field sports, MMA, cricket, esports** come after the game engines because each adds one new
   engine or a constrained source.
5. **Tennis source resolution** runs in parallel with Batch 1 because tennis has the largest
   volume; its blocker is a data question, not a modelling one.
6. **Player props and season-level families** wait for prospective input capture; historical
   results alone cannot establish pre-event inputs for them.

## 7. Risks

- **Short Kalshi history.** Kalshi sports markets start in late 2024 and 2025; benchmark
  comparisons cover roughly two seasons per league. Skill against free closing lines can use
  longer history, but only for NFL and European soccer.
- **Sparse trading.** About 9% of sampled hourly candles contain a trade; quote-based benchmarks
  must apply staleness limits and report coverage.
- **Rule drift.** Contract templates change (7 linked PDFs already 404). Store the rules text and
  terms URL hash per market at ingestion and evaluate each market under its own rules.
- **Source fragility.** ESPN endpoints are undocumented; Sports Reference, FBref, UFCStats, BoxRec,
  and stats.nba.com block or limit automated access. Raw caching and per-adapter health checks are
  required, and licensing notes (MLBAM non-bulk, Liquipedia CC-BY-SA, nflverse CC-BY-4.0) must be
  carried into any derived dataset.
- **Correlation inflating evidence.** Millions of markets reduce to tens of thousands of games and
  tens of tournaments; all reports state effective cluster counts.
- **Shared environment.** The project venv and lock file are shared with the weather collector;
  sports adds no packages to them (section 2.2).

## 8. Explicitly not in Phase 1

Production models or tuning beyond frozen baselines, order placement, paid data, scheduled
collectors, and any change to weather Phase 7 code, schedules, credentials, protocol,
calibration, or artifacts.
