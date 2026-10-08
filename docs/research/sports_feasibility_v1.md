# Sports feasibility audit (v1) - all Kalshi sports categories

Status: RESEARCH_ONLY / NO_BET. Public, unauthenticated reads only. No paid services, no orders,
no scheduled collectors. Weather Phase 7 untouched.

Audit date: 2026-10-08 (discovery crawl run id `20261008T2225Z`).
Companion files: [`sports_inventory_v1.csv`](sports_inventory_v1.csv) (one row per series) and
[`sports_phase1_plan_v1.md`](sports_phase1_plan_v1.md) (architecture, baselines, batches).

## Summary

- **Scope covered.** All 3,871 series Kalshi lists under category Sports were enumerated and every
  one was fully paginated on events, live markets, and archived markets: 387,843 events and
  2,632,386 markets across 24 sport groupings (Kalshi's sport tags, with AFL split out of
  "Football" into Aussie Rules and Olympics, Table Tennis, Cycling, Squash, Rowing kept separate;
  a further 19 series are sports-tagged but not sporting outcomes and 11 one-off novelties remain
  unassigned). No prior sports/NBA work existed in the repo; this is a fresh
  track.
- **Where the volume is.** Tennis (30.4B contracts), basketball (28.9B), soccer (22.9B),
  football (19.1B), and baseball (18.1B) dominate; golf, cricket, hockey, esports, and MMA follow
  at 1.8-5.2B each. Single-game markets (winner, spread, total, segments) hold most of it.
- **Readiness.** Of 178 sport x family cells: 43 A (historical evaluation ready), 27 B (limited),
  47 C (prospective collection), 1 D (blocked: KenPom), 38 E (out of scope), 22 F (no tradable
  history and inactive). A cells are game-level families in baseball, basketball, soccer,
  football, hockey, esports, cricket, MMA, Aussie Rules, lacrosse, volleyball, plus field finishes
  in golf and motorsport.
- **Biggest gap.** Tennis is the largest market by volume but is only B: the free bulk results
  source used by most research (Sackmann `tennis_atp`/`tennis_wta`) is no longer at its GitHub
  location, and tennis-data.co.uk was blocked from this host. ESPN covers main-tour matches only.
- **Kalshi price history** (candles + trades) was returned for 308 of 309 sampled settled
  markets across 157 cells, but only about 9% of hourly candles contain a trade, and there is no
  historical order-book depth. Kalshi history starts in late 2024/2025, so price comparisons are
  limited to roughly two seasons.
- **Inputs vs results.** Free results reach back years for most major sports; free *as-of*
  inputs are mostly limited to what can be derived from earlier results, plus closing lines for
  NFL (nflverse) and European soccer (football-data.co.uk) and dated AFL model tips (Squiggle).
  Injuries, lineups, starters, and weather are overwritten and need prospective capture.

Readiness classes: A = historical evaluation ready, B = limited, C = prospective collection
required, D = blocked by unavailable data, E = out of scope for Phase 1, F = no tradable history
and inactive (section 7).

## 1. Discovery method

### 1.1 Queries

All calls went to `https://external-api.kalshi.com/trade-api/v2` without credentials, through the
repo's `kalshi.client.KalshiClient` (retry with backoff on 429/5xx, no retry on 404). The crawler is
`research/sports/kalshi_discovery.py`; every paginated call is logged to
`data/cache/sports/kalshi_discovery/20261008T2225Z/query_log.jsonl` with parameters, page count,
item count, completion flag, and start/finish timestamps.

| Step | Endpoint and parameters | Pagination | Result |
|---|---|---|---|
| Category taxonomy | `GET /search/tags_by_categories` | single response | Sports is one of 17 categories |
| Sport taxonomy | `GET /search/filters_by_sport` | single response | 19 sports + "All sports"; competitions and scopes per sport |
| Series | `GET /series?category=Sports&include_volume=true&include_product_metadata=true` | single response (no cursor returned) | 3,871 series |
| Events per series | `GET /events?series_ticker=<s>&limit=200` | cursor until empty | all events, including those whose markets are archived |
| Live markets per series | `GET /markets?series_ticker=<s>&limit=1000` (all statuses, MVE excluded by default) | cursor until empty | open, upcoming, closed, and recently settled markets |
| Archived markets per series | `GET /historical/markets?series_ticker=<s>&limit=1000` | cursor until empty | markets settled before the cutoff |
| Milestones | `GET /milestones?category=Sports&limit=500` | 309 pages, complete | 154,335 sports milestones |
| Structured targets | `GET /structured_targets?page_size=2000` | 141 pages, complete | 280,988 targets (all categories) |
| Combo collections | `GET /multivariate_event_collections?limit=200` | 8 pages, complete | 1,406 collections |
| Cutoff | `GET /historical/cutoff` | single | `market_settled_ts` = 2026-08-09T00:00:00Z |
| Price history | candlesticks + trades on sampled settled markets (`research/sports/price_probe.py`) | per market | see section 6 |

Pagination outcome (from `query_log.jsonl`): 11,814 logged paginations, 16,134 pages in total
(events 5,549; historical markets 5,533; live markets 4,588; milestones 309; structured targets
141). The first pass (22:28-23:30 UTC) left 66 paginations in 63 series incomplete after five
consecutive HTTP 429 retries; a resume pass at lower concurrency (23:31-23:35 UTC) completed all
of them. Final state: **3,871 of 3,871 series fully paginated on all three per-series
endpoints**, 2,632,386 markets (1,667,542 archived, 964,844 live tier), 387,843 events. The
largest single paginations were `/historical/markets` for MLB player props (`KXMLBHRR` 150 pages,
`KXMLBTB` 123, `KXMLBHIT` 101).

Offering status at crawl time: 1,341 series active or upcoming, 1,808 historical only, 722 with
no events or markets at all (created but never listed, or placeholders).

### 1.2 Access limitations

- **Rate limits.** Unauthenticated reads started returning HTTP 429 at roughly 8-12 requests per
  second; the crawl ran with five workers and a 0.3 s per-worker delay and recovered every 429 by
  backoff. Documented tier budgets (`docs.kalshi.com/getting_started/rate_limits`) start at 200
  read tokens/s for Basic accounts; endpoint-specific costs (`GET /account/endpoint_costs`)
  require authentication and were **not checked**.
- **Historical tier.** Markets settled before 2026-08-09 are only in `/historical/markets`;
  events and series remain on live endpoints. Both tiers were crawled for every series.
- **Combos (MVE).** `/markets` excludes multivariate markets by default; combo collections were
  inventoried from `/multivariate_event_collections`, but combo markets themselves were not
  paginated (they are derived from leg markets and are out of scope for Phase 1).
- **No order-book history.** The API exposes current order books only; historical depth cannot be
  reconstructed.
- **Volume field.** `/series?include_volume=true` returned `volume_fp` but no `volume`; volumes in
  this report are sums of market-level `volume_fp` (contracts) over both tiers.

### 1.3 Verified absence vs could not check

| Item | Finding | Type |
|---|---|---|
| Sports outside the 19 `filters_by_sport` sports | Series tags also include Olympics, Table Tennis, Cycling, Squash, Aussie Rules, Rowing, Pickleball-style novelties; all are in the inventory | verified (enumerated) |
| Kalshi historical order-book depth | Not offered by any documented endpoint | verified absence (docs) |
| Free historical odds for NBA, MLB, NHL, NCAAB, tennis | ESPN past-game `odds` absent/null in samples; SBR archive page had no file links; tennis-data.co.uk blocked | could not find / could not check |
| Jeff Sackmann `tennis_atp` / `tennis_wta` | GitHub API 404 for both repos; account lists only `tennis_MatchChartingProject` | verified absence at that location |
| `stats.nba.com`, `cdn.nba.com` | connection reset / HTTP 403 from this host | could not check |
| FBref, BoxRec | Cloudflare challenge (HTTP 403) | could not check |
| UFCStats | 3 KB JavaScript "Loading" page | could not check |
| tennis-data.co.uk | HTTP 403 with default and browser user agents | could not check |
| ClubElo API | HTTP 502 on two attempts | could not check (transient?) |
| CollegeFootballData, football-data.org, CFL API | Require a free key (401/403) or failed to connect | not exercised |
| KenPom, Data Golf | Paid | excluded by constraint |
| Kalshi `GET /account/endpoint_costs` | Requires authentication | not checked |
| Seven contract-terms PDFs | HTTP 404 (`BASKETBALLARCHIVED`, `FOOTBALLARCHIVED`, `OLDCRICKETSERIES`, `PGATOURQUALIFY`, `ADS`, `ASIACUPCRICKET`, an empty `.pdf` name) | verified absence at listed URL |

## 2. Inventory overview

Readiness classes used throughout (definitions in section 7):
A = historical evaluation ready, B = historical evaluation limited, C = prospective collection
required, D = blocked by unavailable data, E = out of scope for Phase 1, F = no tradable history
and not active.

### 2.1 By sport

<!-- BEGIN:sport_table -->
| Sport | Series | Active/upcoming series | Settled events (est.) | Markets | Traded markets | Volume (contracts) | First market open | Families | Cells A/B/C/D/E/F | Free results status |
|---|---|---|---|---|---|---|---|---|---|---|
| Tennis | 145 | 31 | 85,836 | 187,605 | 178,621 | 30,383.0M | 2025-01-31 | 12 | 0/6/2/0/3/1 | partial (main tours via ESPN; Challenger/ITF only via Kalshi live data) |
| Basketball | 660 | 217 | 51,569 | 397,500 | 347,205 | 28,870.2M | 2024-12-19 | 19 | 7/2/5/1/4/0 | free |
| Soccer | 1,443 | 489 | 73,169 | 347,491 | 286,056 | 22,937.7M | 2025-01-31 | 16 | 7/2/3/0/4/0 | free |
| Football | 624 | 302 | 19,109 | 274,091 | 227,372 | 19,101.7M | 2024-12-04 | 18 | 6/1/7/0/4/0 | free |
| Baseball | 236 | 86 | 68,625 | 950,593 | 749,718 | 18,104.9M | 2025-02-13 | 17 | 6/2/4/0/3/2 | free (MLBAM non-commercial/non-bulk notice) |
| Golf | 134 | 33 | 4,205 | 108,906 | 69,177 | 5,172.6M | 2025-02-07 | 10 | 1/1/3/0/2/3 | free (leaderboards) |
| Cricket | 56 | 7 | 2,940 | 6,894 | 6,548 | 2,550.3M | 2025-04-01 | 8 | 1/2/0/0/1/4 | free |
| Hockey | 98 | 57 | 12,271 | 173,286 | 80,046 | 2,235.1M | 2025-01-23 | 16 | 6/2/4/0/2/2 | free |
| Esports | 133 | 25 | 47,828 | 90,666 | 85,953 | 1,883.7M | 2025-01-31 | 8 | 3/0/2/0/2/1 | free with rate-limit/approval constraints |
| MMA | 51 | 19 | 2,886 | 11,404 | 10,863 | 1,782.9M | 2025-02-07 | 5 | 2/0/2/0/1/0 | free (ESPN, Kalshi live data); UFCStats blocked |
| Motorsport | 69 | 35 | 697 | 20,367 | 16,255 | 323.8M | 2025-02-12 | 6 | 1/1/2/0/2/0 | free |
| Boxing | 20 | 13 | 423 | 1,148 | 1,028 | 256.7M | 2025-05-02 | 4 | 0/0/3/0/1/0 | partial |
| Other | 48 | 8 | 133 | 1,563 | 918 | 32.9M | 2025-06-18 | 5 | 0/1/2/0/1/1 | unverified |
| Olympics | 40 | 0 | 373 | 7,681 | 3,226 | 27.8M | 2026-01-20 | 2 | 0/1/0/0/0/1 | partial (HTML) |
| Table Tennis | 12 | 0 | 15,211 | 30,448 | 25,002 | 19.6M | 2026-01-15 | 2 | 0/1/0/0/0/1 | Kalshi-only in practice |
| Aussie Rules | 1 | 0 | 224 | 448 | 447 | 7.7M | 2026-03-04 | 1 | 1/0/0/0/0/0 | free |
| Chess | 31 | 6 | 151 | 6,026 | 2,602 | 7.5M | 2025-02-13 | 6 | 0/1/4/0/1/0 | partial |
| Lacrosse | 11 | 2 | 505 | 1,210 | 1,158 | 5.7M | 2026-01-31 | 3 | 1/0/1/0/0/1 | free |
| Rugby | 15 | 5 | 539 | 1,679 | 1,435 | 3.1M | 2026-01-22 | 2 | 0/1/1/0/0/0 | partial |
| Unassigned | 11 | 0 | 10 | 101 | 96 | 2.7M | 2025-07-16 | 4 | 0/0/0/0/3/1 | unverified |
| Cycling | 4 | 0 | 52 | 9,246 | 897 | 2.2M | 2026-05-08 | 3 | 0/1/0/0/0/2 | unverified |
| Non-sport (sports-tagged) | 19 | 2 | 12 | 187 | 148 | 1.9M | 2025-02-08 | 4 | 0/0/0/0/4/0 | unverified |
| Volleyball | 3 | 2 | 800 | 1,765 | 1,541 | 1.7M | 2026-08-13 | 2 | 1/0/1/0/0/0 | free |
| Darts | 3 | 2 | 617 | 1,400 | 1,222 | 1.3M | 2025-12-11 | 2 | 0/1/1/0/0/0 | partial (HTML) |
| Squash | 3 | 0 | 173 | 512 | 258 | 300k | 2026-04-13 | 2 | 0/1/0/0/0/1 | unverified |
| Rowing | 1 | 0 | 8 | 169 | 11 | 3k | 2025-10-17 | 1 | 0/0/0/0/0/1 | unverified |
<!-- END:sport_table -->

### 2.2 Contract families

Families are research groupings derived from Kalshi's `product_metadata.scope` (367 distinct
values) with a title/ticker fallback for the 494 series without a scope
(`research/sports/families.py`). The raw scope is kept in the CSV for every series.

| Family | Definition | Examples |
|---|---|---|
| game_winner | Winner of a single game/match/fight (incl. Tie strike) | `KXMLBGAME`, `KXNBAGAME`, `KXEPLGAME`, `KXATPMATCH`, `KXUFCFIGHT` |
| spread | Margin thresholds for a full game | `KXNFLSPREAD`, `KXNBASPREAD`, `KXEPLSPREAD` |
| total | Combined score/count thresholds for a full game | `KXNBATOTAL`, `KXMLBTOTAL`, `KXATPGTOTAL` |
| team_total | One team's score thresholds | `KXNFLTEAMTOTAL`, soccer `TEAMTOTAL` |
| segment | Winner/spread/total of a half, quarter, period, inning, set, or map | `KXNBA1HSPREAD`, `KXNHL3P`, `KXNCAAF4Q` |
| score_props | Outcomes derived from the joint score or scoring sequence (BTTS, correct score, first to score, overtime, method/round of finish, corners) | `KXEPLBTTS`, `KXUFCVICROUND` |
| player_prop | One player's single-game statistic | `KXMLBHIT`, `KXNBAPTS`, `KXEPLGOAL`, `KXNFLPASSYDS` |
| field_finish | Placement in a many-competitor event, H2H matchups within fields | `KXPGATOUR`, `KXF1RACE`, `KXF1H2H`, Olympics events |
| tournament_outright | Champion, to advance, qualification, relegation | `KXNHL`, `KXMLBWS`, `KXUCL`, `KXAFCCLADVANCE` |
| series_playoff | Playoff series winner/length/stat leaders | `KXNBASERIESPTSLEADER`, `KXWNBASERIESSCORE` |
| season_wins | Season win totals, records, streaks, seeds | `KXNHLWINS`, `KXNFLEXACTWINS*` |
| league_leaders | Season statistical leaders, fantasy leaders | `KXLEADERMLBAVG`, `KXLEADERNFLAPYDS` |
| awards | MVP, rookie, All-Star, Hall of Fame | `KXNBAMVP`, `KXHEISMAN` |
| rankings_polls | AP/CFP polls, KenPom, rankings, top-100 lists | `KXNCAAMBKENPOMTOP`, `KXNFLT100` |
| draft | Draft order, picks, lottery | `KXNFLDRAFT1ST`, `KXNBALOTTERYODDS` |
| personnel_ops | Coaches, trades, signings, transfers, expansion, venues | `KXNEXTNFLCOACH`, `KXMLBTRADE` |
| combo_parlay | Kalshi "pre-pack"/combo series | `KXNFLPREPACK*` |
| other_novelty, test_placeholder | Everything else; test/placeholder series | `KXHONEYDEUCE`, `KXEPLTESTIMAGE` |

## 3. Consolidated coverage matrix

One row per discovered sport x contract family. "Settled events (est.)" counts archived events
plus live-tier events in proportion to their settled markets. "Free results sources" are the top
sources from the sport's assessment (section 5). "Kalshi price history" is the result of the
section 6 probe for that cell ("not sampled" when the cell had no settled traded example).

<!-- BEGIN:matrix -->
| Sport | Contract family | Series (active/upcoming) | Settled events (est.) | Markets (archived) | Traded mkts | Volume | First open | Kalshi settlement sources (top) | Free results sources | Pre-event inputs (as-of) | Kalshi price history | Readiness | Blockers / notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Tennis | game_winner | 34 (11) | 66,535 | 133,760 (93,794) | 130,680 | 29,175.7M | 2025-05-25 | ATP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical+live) | B | independent results source partial |
| Tennis | segment | 10 (2) | 11,380 | 22,882 (15,860) | 21,203 | 663.5M | 2026-01-19 | ATP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical+live) | B | independent results source partial; needs period/half/inning line scores; OT excluded from quarters/halves |
| Tennis | tournament_outright | 75 (8) | 405 | 4,373 (3,155) | 2,852 | 373.8M | 2025-01-31 | AP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Tennis | score_props | 3 (1) | 2,441 | 10,838 (6,426) | 10,034 | 71.2M | 2026-01-19 | ATP, ESPN, WTA | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical+live) | B | independent results source partial; needs scoring-event timing (PBP) for first-scorer/timing props |
| Tennis | total | 4 (2) | 2,934 | 8,974 (4,461) | 7,980 | 61.4M | 2026-01-19 | ATP, WTA | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical) | B | independent results source partial; needs score-total distribution; OT/shootout counts per rules |
| Tennis | spread | 2 (1) | 2,073 | 6,571 (4,228) | 5,740 | 36.6M | 2026-03-06 | ATP, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (live) | B | independent results source partial; needs score-margin distribution; OT counts per rules |
| Tennis | personnel_ops | 7 (1) | 10 | 41 (13) | 38 | 593k | 2025-07-18 | ATP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | - | verified 2/2 (live) | E | not a modelable sporting outcome or derived combo |
| Tennis | rankings_polls | 3 (3) | 1 | 40 (4) | 22 | 186k | 2026-03-02 | ATP, WTA | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; poll/ranking processes; KenPom is paid |
| Tennis | field_finish | 1 (0) | 1 | 8 (8) | 8 | 68k | 2026-06-22 | ESPN, WTA | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical) | F | too few settled events; inactive; needs full-field results; tie rules (golf ties count as position) |
| Tennis | player_prop | 2 (0) | 55 | 110 (110) | 56 | 50k | 2026-06-03 | ATP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | Player Elo derivable from results; rankings as-of require archive (Sackmann repos absent) | verified 2/2 (historical) | B | independent results source partial; player availability/minutes not reconstructable as-of; DNP rules vary |
| Tennis | other_novelty | 3 (2) | 1 | 8 (1) | 8 | 21k | 2025-05-24 | ESPN, Reuters, The New York Times | ESPN site API, Kalshi live data / game stats | - | verified 1/1 (historical) | E | not a modelable sporting outcome or derived combo |
| Tennis | test_placeholder | 1 (0) | 0 | 0 (0) | 0 | 0 |  | ATP | ESPN site API, Kalshi live data / game stats | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Basketball | game_winner | 93 (24) | 16,055 | 32,858 (30,674) | 32,291 | 20,436.6M | 2025-02-11 | 365 Scores, ESPN, Flashscore | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical) | A |  |
| Basketball | spread | 30 (3) | 7,199 | 76,627 (70,506) | 75,981 | 2,841.2M | 2025-10-21 | ESPN, ESPN, Flashscore | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (live) | A | needs score-margin distribution; OT counts per rules |
| Basketball | total | 38 (8) | 7,240 | 73,992 (67,731) | 73,010 | 2,433.8M | 2025-10-15 | ESPN, Flashscore, Sofascore | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical) | A | needs score-total distribution; OT/shootout counts per rules |
| Basketball | tournament_outright | 139 (64) | 305 | 6,625 (3,623) | 3,649 | 1,630.5M | 2025-01-23 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | B | many advance/series events but correlated within tournaments; few independent outcomes; needs schedule/bracket simulation |
| Basketball | segment | 61 (21) | 10,462 | 65,994 (52,522) | 44,468 | 368.5M | 2026-02-10 | ESPN, ESPN, Kalshi using information originating from the NCAA | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Basketball | series_playoff | 12 (2) | 82 | 466 (426) | 458 | 366.4M | 2025-04-18 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; few series per season; needs game model + simulation |
| Basketball | personnel_ops | 55 (24) | 72 | 2,706 (997) | 1,757 | 304.2M | 2024-12-19 | ESPN, Fox Sports, Reuters | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Basketball | player_prop | 34 (1) | 8,727 | 116,537 (109,678) | 101,308 | 221.1M | 2025-11-18 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical) | B | results-only usage baseline; availability inputs missing; player availability/minutes not reconstructable as-of; DNP rules vary |
| Basketball | awards | 61 (33) | 48 | 3,365 (1,211) | 1,554 | 174.8M | 2025-06-02 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Basketball | team_total | 2 (1) | 631 | 11,503 (9,486) | 9,013 | 34.9M | 2026-02-09 | ESPN, the Governing League, the Governing League | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical+live) | A | needs per-team scoring distribution |
| Basketball | draft | 38 (1) | 79 | 1,888 (1,861) | 1,397 | 18.7M | 2025-06-13 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; mock-draft consensus not archived as-of |
| Basketball | score_props | 10 (1) | 485 | 595 (465) | 593 | 14.9M | 2026-03-17 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (historical) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| Basketball | season_wins | 29 (16) | 25 | 1,684 (307) | 419 | 10.0M | 2025-10-28 | ESPN, Flashscore, Kalshi using information originating from the NCAA | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; one outcome per team-season; needs as-of standings + simulation |
| Basketball | combo_parlay | 4 (0) | 17 | 126 (122) | 122 | 8.2M | 2025-12-23 | ESPN, Fox Sports, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Basketball | league_leaders | 18 (3) | 96 | 1,209 (912) | 762 | 3.5M | 2025-11-25 | ESPN, Fox Sports, Fox Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Basketball | other_novelty | 18 (5) | 13 | 177 (134) | 98 | 2.5M | 2025-06-05 | ESPN, Fox Sports, Reuters | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Basketball | field_finish | 11 (6) | 31 | 519 (2) | 206 | 72k | 2026-05-29 | ESPN, FIBA, Flashscore | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | Ratings derivable from results (as-of exact); rest/travel from schedules; injuries/lineups NOT reconstructable (Kalshi milestone injury notes are overwritten) | verified 2/2 (live) | A | needs full-field results; tie rules (golf ties count as position) |
| Basketball | rankings_polls | 6 (4) | 2 | 629 (10) | 119 | 66k | 2026-02-23 | AP College Basketball Rankings, AP Women's College Basketball Rankings, KenPom | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | D | settles on paid KenPom ratings; poll/ranking processes; KenPom is paid |
| Basketball | test_placeholder | 1 (0) | 0 | 0 (0) | 0 | 0 |  | Associated Press, Bleacher Report, CBS Sports | ESPN site API, Kalshi live data / game stats, NCAA.com scoreboard JSON | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Soccer | game_winner | 134 (75) | 17,248 | 54,560 (31,715) | 52,171 | 14,790.4M | 2025-05-06 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (live) | A |  |
| Soccer | total | 129 (52) | 10,592 | 67,145 (20,405) | 64,199 | 2,016.3M | 2025-11-04 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 1/2 (live) | A | needs score-total distribution; OT/shootout counts per rules |
| Soccer | score_props | 282 (72) | 13,625 | 69,019 (24,319) | 46,984 | 1,928.5M | 2025-11-04 | ESPN, ESPN, ESPN | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (live) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| Soccer | tournament_outright | 296 (93) | 2,849 | 13,635 (8,406) | 10,725 | 1,100.6M | 2025-01-31 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | B | many advance/series events but correlated within tournaments; few independent outcomes; needs schedule/bracket simulation |
| Soccer | segment | 277 (99) | 15,207 | 42,431 (10,384) | 35,975 | 1,060.4M | 2026-03-02 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (live) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Soccer | spread | 127 (53) | 10,580 | 49,134 (17,568) | 46,706 | 983.0M | 2025-11-04 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (live) | A | needs score-margin distribution; OT counts per rules |
| Soccer | player_prop | 40 (0) | 1,356 | 35,677 (29,311) | 16,132 | 708.8M | 2025-11-25 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (historical+live) | B | results-only usage baseline; availability inputs missing; player availability/minutes not reconstructable as-of; DNP rules vary |
| Soccer | team_total | 57 (13) | 1,477 | 12,015 (3,216) | 10,042 | 226.7M | 2026-05-16 | ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (live) | A | needs per-team scoring distribution |
| Soccer | awards | 12 (3) | 17 | 548 (431) | 405 | 76.7M | 2025-06-04 | ESPN, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Soccer | field_finish | 9 (3) | 69 | 571 (449) | 526 | 24.7M | 2026-05-08 | Ballon d'Or, ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | Ratings/Poisson strengths from results (as-of exact); dated Elo (ClubElo, unreachable on audit day); lineups not reconstructable | verified 2/2 (historical) | A | needs full-field results; tie rules (golf ties count as position) |
| Soccer | personnel_ops | 47 (12) | 123 | 2,357 (1,747) | 2,024 | 16.4M | 2025-05-15 | ESPN, ESPN, Reuters | football-data.co.uk, openfootball football.json, ESPN site API | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Soccer | combo_parlay | 1 (0) | 5 | 6 (6) | 6 | 4.0M | 2026-07-06 | ESPN, FIFA | football-data.co.uk, openfootball football.json, ESPN site API | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Soccer | season_wins | 3 (1) | 13 | 55 (54) | 55 | 950k | 2026-02-11 | ESPN, FIFA, MLB | football-data.co.uk, openfootball football.json, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; one outcome per team-season; needs as-of standings + simulation |
| Soccer | other_novelty | 15 (4) | 8 | 40 (21) | 33 | 121k | 2025-05-22 | BBC Sport, ESPN, Fox Sports | football-data.co.uk, openfootball football.json, ESPN site API | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Soccer | league_leaders | 9 (9) | 0 | 298 (0) | 73 | 106k | 2026-08-06 | CBS Sports, ESPN, Fox Sports | football-data.co.uk, openfootball football.json, ESPN site API | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Soccer | test_placeholder | 5 (0) | 0 | 0 (0) | 0 | 0 |  | ESPN, ESPN, Fox Sports | football-data.co.uk, openfootball football.json, ESPN site API | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Football | game_winner | 22 (5) | 2,235 | 5,023 (2,785) | 4,838 | 12,792.5M | 2025-05-20 | ESPN, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (historical) | A |  |
| Football | spread | 4 (3) | 1,926 | 38,851 (18,024) | 34,152 | 2,142.3M | 2025-08-19 | CFL, ESPN, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (historical) | A | needs score-margin distribution; OT counts per rules |
| Football | total | 11 (10) | 1,947 | 31,633 (13,936) | 27,517 | 1,210.7M | 2025-08-19 | CFL, ESPN, ESPN | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (live) | A | needs score-total distribution; OT/shootout counts per rules |
| Football | tournament_outright | 77 (56) | 47 | 3,784 (806) | 2,190 | 1,078.1M | 2025-01-23 | ESPN, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Football | player_prop | 93 (54) | 3,955 | 83,058 (39,776) | 70,898 | 845.6M | 2025-09-04 | ESPN, ESPN, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (live) | B | results-only usage baseline; availability inputs missing; player availability/minutes not reconstructable as-of; DNP rules vary |
| Football | segment | 48 (38) | 6,181 | 68,729 (445) | 59,719 | 412.4M | 2026-01-15 | ESPN, ESPN, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (live) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Football | combo_parlay | 12 (0) | 177 | 1,014 (978) | 968 | 189.3M | 2025-09-04 | Kalshi using information originating from the NCAA, the league governing the game | nflverse, ESPN site API, Kalshi live data / game stats | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Football | awards | 48 (24) | 16 | 3,708 (422) | 1,111 | 159.2M | 2025-06-14 | ESPN, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Football | season_wins | 79 (12) | 99 | 2,569 (1,584) | 1,369 | 71.8M | 2025-05-17 | ESPN, ESPN, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; one outcome per team-season; needs as-of standings + simulation |
| Football | personnel_ops | 86 (21) | 102 | 2,782 (1,526) | 2,087 | 67.4M | 2024-12-04 | ABC, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Football | team_total | 2 (2) | 441 | 12,283 (809) | 10,848 | 53.0M | 2025-12-03 | ESPN, NCAA, the Governing League | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (live) | A | needs per-team scoring distribution |
| Football | draft | 28 (3) | 145 | 2,957 (2,832) | 2,404 | 42.9M | 2025-06-18 | ESPN, ESPN, The Wall Street Journal | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; mock-draft consensus not archived as-of |
| Football | score_props | 29 (18) | 1,673 | 5,405 (164) | 4,595 | 26.9M | 2026-01-28 | ESPN, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | verified 2/2 (historical+live) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| Football | league_leaders | 43 (36) | 107 | 6,370 (28) | 2,832 | 4.6M | 2025-09-18 | ESPN, ESPN Fantasy, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Football | other_novelty | 21 (5) | 28 | 570 (23) | 449 | 3.4M | 2025-05-24 | ESPN, ESPN, Fox Sports | nflverse, ESPN site API, Kalshi live data / game stats | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Football | rankings_polls | 11 (8) | 30 | 4,931 (0) | 1,037 | 1.4M | 2025-12-29 | AP College Football Rankings, College Football Playoff Rankings, College Gameday | nflverse, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; inputs need live capture; poll/ranking processes; KenPom is paid |
| Football | field_finish | 8 (7) | 0 | 424 (0) | 358 | 260k | 2026-02-25 | ESPN, Kalshi using information originating from the NCAA, NBC Sports | nflverse, ESPN site API, Kalshi live data / game stats | Ratings from results; closing lines usable only as a market-proxy at close; QB starter and injury status not reconstructable as-of | not sampled | C | too few settled events; needs full-field results; tie rules (golf ties count as position) |
| Football | test_placeholder | 2 (0) | 0 | 0 (0) | 0 | 0 |  | ESPN, Kalshi using information originating from the NCAA | nflverse, ESPN site API, Kalshi live data / game stats | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Baseball | game_winner | 11 (3) | 14,757 | 29,577 (26,637) | 29,288 | 11,270.4M | 2025-04-01 | ESPN, ESPN, Fox Sports | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (historical+live) | A |  |
| Baseball | total | 7 (4) | 3,636 | 36,028 (24,288) | 35,752 | 2,701.4M | 2025-09-30 | ESPN, Flashscore, KBO | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (live) | A | needs score-total distribution; OT/shootout counts per rules |
| Baseball | spread | 6 (4) | 3,628 | 26,323 (17,411) | 26,045 | 1,530.7M | 2025-09-30 | ESPN, KBO, Kalshi using information originating from the NCAA | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (historical+live) | A | needs score-margin distribution; OT counts per rules |
| Baseball | player_prop | 31 (13) | 18,367 | 744,227 (485,627) | 551,319 | 824.2M | 2026-02-05 | ESPN, ESPN, Fox Sports | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (historical+live) | B | results-only usage baseline; availability inputs missing; player availability/minutes not reconstructable as-of; DNP rules vary |
| Baseball | score_props | 6 (4) | 5,483 | 5,512 (3,384) | 5,418 | 561.8M | 2025-10-16 | AP, ESPN, Fox Sports | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (live) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| Baseball | segment | 10 (7) | 19,943 | 67,382 (28,158) | 62,718 | 561.8M | 2026-03-04 | CBS Sports, ESPN, World Baseball Classic | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (historical+live) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Baseball | tournament_outright | 38 (12) | 87 | 1,159 (570) | 960 | 307.4M | 2025-02-13 | ESPN, ESPN, Fox Sports | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; needs schedule/bracket simulation |
| Baseball | team_total | 1 (1) | 2,470 | 34,594 (23,772) | 33,732 | 138.4M | 2026-03-31 | ESPN, Fox Sports, the Governing League | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (live) | A | needs per-team scoring distribution |
| Baseball | field_finish | 12 (0) | 14 | 224 (224) | 224 | 76.1M | 2025-07-08 | ESPN, the Governing League | MLB Stats API, Retrosheet, ESPN site API | Ratings from results; probable starting pitchers are the key input and are NOT reconstructable as-of (historical feed shows the actual starter) | verified 2/2 (historical) | B | few settled events; needs full-field results; tie rules (golf ties count as position) |
| Baseball | series_playoff | 2 (2) | 23 | 84 (18) | 82 | 69.0M | 2025-09-29 | ESPN, the Governing League | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; few series per season; needs game model + simulation |
| Baseball | awards | 33 (21) | 49 | 2,131 (893) | 1,539 | 51.5M | 2025-06-13 | ESPN, Fox Sports, World Baseball Classic | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Baseball | personnel_ops | 14 (7) | 49 | 1,569 (649) | 1,285 | 4.1M | 2025-07-24 | ESPN, ESPN, Fox Sports | MLB Stats API, Retrosheet, ESPN site API | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Baseball | season_wins | 35 (0) | 71 | 502 (170) | 438 | 3.7M | 2025-05-21 | ESPN, ESPN, The Wall Street Journal | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; one outcome per team-season; needs as-of standings + simulation |
| Baseball | other_novelty | 11 (3) | 13 | 114 (73) | 109 | 2.1M | 2025-06-13 | ESPN, Fox Sports, The Wall Street Journal | MLB Stats API, Retrosheet, ESPN site API | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Baseball | league_leaders | 15 (4) | 26 | 920 (22) | 654 | 1.5M | 2026-02-05 | Baseball Reference, ESPN, ESPN | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Baseball | draft | 2 (0) | 8 | 240 (240) | 148 | 601k | 2026-05-08 | the Governing League | MLB Stats API, Retrosheet, ESPN site API | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; mock-draft consensus not archived as-of |
| Baseball | combo_parlay | 2 (1) | 1 | 7 (6) | 7 | 382k | 2026-03-17 | World Baseball Classic, the Governing League | MLB Stats API, Retrosheet, ESPN site API | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Golf | field_finish | 67 (19) | 3,939 | 95,102 (71,456) | 58,457 | 4,776.3M | 2025-02-18 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | Field strength from prior finishes (derivable); weekly OWGR is dated | verified 2/2 (historical) | A | needs full-field results; tie rules (golf ties count as position) |
| Golf | tournament_outright | 32 (4) | 65 | 6,925 (4,766) | 4,909 | 366.0M | 2025-02-07 | AP, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Golf | player_prop | 13 (2) | 169 | 6,283 (5,306) | 5,400 | 27.3M | 2026-02-03 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | Field strength from prior finishes (derivable); weekly OWGR is dated | verified 2/2 (historical+live) | B | small settled sample; player availability/minutes not reconstructable as-of; DNP rules vary |
| Golf | league_leaders | 3 (1) | 4 | 54 (1) | 54 | 1.7M | 2025-12-17 | Bryson DeChambeau, ESPN, ESPN | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Golf | segment | 2 (0) | 5 | 10 (0) | 10 | 638k | 2026-09-09 | ESPN, ESPN, the Governing League | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | Field strength from prior finishes (derivable); weekly OWGR is dated | verified 2/2 (live) | F | too few settled events; inactive; needs period/half/inning line scores; OT excluded from quarters/halves |
| Golf | personnel_ops | 4 (2) | 8 | 61 (44) | 61 | 355k | 2026-04-15 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Golf | other_novelty | 8 (4) | 6 | 254 (67) | 84 | 239k | 2026-01-13 | ESPN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Golf | score_props | 1 (0) | 1 | 5 (0) | 5 | 132k | 2026-09-23 | ESPN, the Governing League | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | Field strength from prior finishes (derivable); weekly OWGR is dated | verified 2/2 (live) | F | too few settled events; inactive; needs scoring-event timing (PBP) for first-scorer/timing props |
| Golf | season_wins | 3 (1) | 2 | 11 (1) | 8 | 36k | 2026-02-17 | AP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; inputs need live capture; one outcome per team-season; needs as-of standings + simulation |
| Golf | rankings_polls | 1 (0) | 6 | 201 (131) | 189 | 32k | 2026-04-27 | the Official World Golf Ranking | ESPN site API, Kalshi live data / game stats, Official World Golf Ranking | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | F | few outcomes; inactive; poll/ranking processes; KenPom is paid |
| Cricket | game_winner | 23 (7) | 2,578 | 5,358 (3,324) | 5,131 | 2,519.8M | 2025-04-28 | BBC Sport, Cricbuzz, ESPN | Cricsheet, Kalshi live data / game stats | Team ratings derivable; toss/XI announced shortly before start - not reconstructable | verified 2/2 (live) | A |  |
| Cricket | tournament_outright | 13 (0) | 101 | 278 (150) | 271 | 24.7M | 2025-04-01 | Cricbuzz, ESPN, ESPNcricinfo | Cricsheet, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few independent outcomes; needs schedule/bracket simulation |
| Cricket | team_total | 4 (0) | 137 | 822 (606) | 807 | 5.3M | 2026-04-07 | Cricbuzz, ESPN, ESPN cricinfo | Cricsheet, Kalshi live data / game stats | Team ratings derivable; toss/XI announced shortly before start - not reconstructable | verified 2/2 (historical+live) | B | small settled sample; needs per-team scoring distribution |
| Cricket | score_props | 5 (0) | 122 | 402 (402) | 330 | 547k | 2026-02-27 | ESPNcricinfo, IPL, International Cricket Council (ICC) | Cricsheet, Kalshi live data / game stats | Team ratings derivable; toss/XI announced shortly before start - not reconstructable | verified 2/2 (historical) | B | small settled sample; needs scoring-event timing (PBP) for first-scorer/timing props |
| Cricket | awards | 2 (0) | 2 | 34 (34) | 9 | 33k | 2026-05-19 | BBC Sport, Cricbuzz, ESPN | Cricsheet, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; voting outcomes; no free as-of odds/straw-poll history |
| Cricket | player_prop | 6 (0) | 0 | 0 (0) | 0 | 0 |  | ESPN, Fox Sports, IPL | Cricsheet, Kalshi live data / game stats | Team ratings derivable; toss/XI announced shortly before start - not reconstructable | not sampled | F | no traded markets; not currently listed; player availability/minutes not reconstructable as-of; DNP rules vary |
| Cricket | test_placeholder | 1 (0) | 0 | 0 (0) | 0 | 0 |  | ESPNcricinfo, International Cricket Council (ICC) | Cricsheet, Kalshi live data / game stats | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Cricket | total | 2 (0) | 0 | 0 (0) | 0 | 0 |  | ESPNcricinfo, International Cricket Council (ICC) | Cricsheet, Kalshi live data / game stats | Team ratings derivable; toss/XI announced shortly before start - not reconstructable | not sampled | F | no traded markets; not currently listed; needs score-total distribution; OT/shootout counts per rules |
| Hockey | game_winner | 13 (9) | 4,363 | 9,392 (7,928) | 9,141 | 1,668.0M | 2025-04-18 | AHL, ESPN, ESPN | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical) | A |  |
| Hockey | total | 5 (2) | 1,414 | 9,475 (7,865) | 9,222 | 227.1M | 2025-10-22 | ESPN, ESPN, ESPN | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical) | A | needs score-total distribution; OT/shootout counts per rules |
| Hockey | tournament_outright | 18 (12) | 20 | 899 (424) | 546 | 101.4M | 2025-01-23 | ESPN, ESPN, Fox Sports | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Hockey | spread | 2 (1) | 1,421 | 5,983 (5,214) | 5,894 | 100.8M | 2025-10-22 | ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical) | A | needs score-margin distribution; OT counts per rules |
| Hockey | field_finish | 7 (0) | 67 | 512 (480) | 464 | 42.9M | 2026-01-29 | ESPN, Flashscore, Fox Sports | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical) | A | needs full-field results; tie rules (golf ties count as position) |
| Hockey | series_playoff | 4 (0) | 42 | 209 (209) | 206 | 41.9M | 2025-04-18 | ESPN, ESPN, Fox Sports | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few series per season; needs game model + simulation |
| Hockey | player_prop | 8 (6) | 3,157 | 116,576 (109,761) | 38,610 | 27.8M | 2025-11-22 | ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical+live) | B | results-only usage baseline; availability inputs missing; player availability/minutes not reconstructable as-of; DNP rules vary |
| Hockey | score_props | 9 (3) | 1,207 | 26,436 (24,063) | 13,197 | 15.4M | 2025-11-22 | ESPN, ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (historical+live) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| Hockey | awards | 11 (8) | 9 | 524 (246) | 318 | 4.5M | 2025-06-05 | ESPN, ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Hockey | segment | 9 (9) | 505 | 1,933 (0) | 1,572 | 3.7M | 2026-09-27 | ESPN, Flashscore, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (live) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Hockey | team_total | 1 (1) | 55 | 730 (0) | 576 | 809k | 2026-09-27 | ESPN, the National Hockey League | NHL web API, ESPN site API, Kalshi live data / game stats | Ratings from results; starting goalie not reconstructable as-of | verified 2/2 (live) | B | small settled sample; needs per-team scoring distribution |
| Hockey | draft | 2 (0) | 8 | 121 (121) | 107 | 737k | 2026-05-05 | ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; mock-draft consensus not archived as-of |
| Hockey | personnel_ops | 4 (2) | 3 | 345 (49) | 125 | 134k | 2026-03-31 | AP, ESPN, ESPN | NHL web API, ESPN site API, Kalshi live data / game stats | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Hockey | league_leaders | 2 (2) | 0 | 118 (0) | 36 | 5k | 2026-09-08 | ESPN, ESPN, NHL | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Hockey | other_novelty | 2 (1) | 0 | 1 (0) | 1 | 2k | 2026-09-01 | ESPN, ESPN, Fox Sports | NHL web API, ESPN site API, Kalshi live data / game stats | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Hockey | season_wins | 1 (1) | 0 | 32 (0) | 31 | 695 | 2026-09-28 | ESPN, National Hockey League | NHL web API, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; one outcome per team-season; needs as-of standings + simulation |
| Esports | game_winner | 20 (8) | 15,589 | 32,277 (19,729) | 30,704 | 1,336.8M | 2025-09-24 | B03, Gamers World, HLTV | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | Team ratings derivable; roster changes partially in Liquipedia history | verified 2/2 (historical+live) | A |  |
| Esports | segment | 8 (5) | 23,279 | 46,958 (29,188) | 45,332 | 500.4M | 2025-10-27 | B03, BO3, Gamers World | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | Team ratings derivable; roster changes partially in Liquipedia history | verified 2/2 (live) | A | needs period/half/inning line scores; OT excluded from quarters/halves |
| Esports | total | 9 (4) | 8,864 | 9,786 (5,415) | 8,903 | 29.6M | 2026-01-13 | Call of Duty League, DLTV, Gamers World | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | Team ratings derivable; roster changes partially in Liquipedia history | verified 2/2 (live) | A | needs score-total distribution; OT/shootout counts per rules |
| Esports | tournament_outright | 66 (6) | 84 | 1,442 (1,138) | 921 | 16.7M | 2025-01-31 | ESPN, Gamers World, HLTV | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Esports | other_novelty | 12 (0) | 9 | 45 (15) | 20 | 90k | 2025-08-13 | AP, ESPN, Fox Sports | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Esports | awards | 9 (2) | 3 | 158 (18) | 73 | 36k | 2025-08-13 | AP, ESPN, Fox Sports | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Esports | spread | 8 (0) | 0 | 0 (0) | 0 | 0 |  | Call of Duty League, DLTV, HLTV | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | Team ratings derivable; roster changes partially in Liquipedia history | not sampled | F | no traded markets; not currently listed; needs score-margin distribution; OT counts per rules |
| Esports | test_placeholder | 1 (0) | 0 | 0 (0) | 0 | 0 |  | Riot Games, VALORANT Esports  | Liquipedia MediaWiki/LPDB API, OpenDota API, Oracle's Elixir LoL data | - | not sampled | E | not a modelable sporting outcome or derived combo |
| MMA | game_winner | 6 (1) | 762 | 1,561 (1,167) | 1,552 | 1,415.9M | 2025-05-08 | DAZN, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | Fighter records derivable; late replacements not reconstructable | verified 2/2 (historical) | A |  |
| MMA | score_props | 8 (5) | 2,106 | 9,229 (5,565) | 8,974 | 359.7M | 2026-01-21 | CBS Sports, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | Fighter records derivable; late replacements not reconstructable | verified 2/2 (live) | A | needs scoring-event timing (PBP) for first-scorer/timing props |
| MMA | tournament_outright | 28 (9) | 14 | 552 (348) | 295 | 4.7M | 2025-02-07 | ESPN, Fox Sports, Fox Sports | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| MMA | personnel_ops | 8 (3) | 4 | 14 (11) | 14 | 2.6M | 2025-06-27 | ESPN, Fox Sports, NBC Sports | ESPN site API, Kalshi live data / game stats | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| MMA | awards | 1 (1) | 0 | 48 (0) | 28 | 20k | 2026-07-30 | AP, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Motorsport | field_finish | 40 (21) | 602 | 17,352 (11,705) | 14,778 | 289.8M | 2025-03-14 | ESPN, FIA, The Wall Street Journal | Jolpica-F1, OpenF1, NASCAR cacher JSON | Qualifying/grid is public before the race and reconstructable from timed results | verified 2/2 (historical) | A | needs full-field results; tie rules (golf ties count as position) |
| Motorsport | tournament_outright | 14 (7) | 9 | 353 (114) | 324 | 32.1M | 2025-02-12 | AP, ESPN, ESPN | Jolpica-F1, OpenF1, NASCAR cacher JSON | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Motorsport | player_prop | 5 (2) | 79 | 2,591 (1,647) | 1,109 | 1.6M | 2025-11-19 | ESPN, FIA, FIA | Jolpica-F1, OpenF1, NASCAR cacher JSON | Qualifying/grid is public before the race and reconstructable from timed results | verified 2/2 (historical) | B | small settled sample; player availability/minutes not reconstructable as-of; DNP rules vary |
| Motorsport | personnel_ops | 6 (4) | 4 | 22 (4) | 18 | 216k | 2025-02-18 | ESPN, FIA, Formula 1 | Jolpica-F1, OpenF1, NASCAR cacher JSON | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Motorsport | other_novelty | 3 (0) | 3 | 3 (3) | 3 | 34k | 2025-02-21 | ABC, Axios, CBS | Jolpica-F1, OpenF1, NASCAR cacher JSON | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Motorsport | awards | 1 (1) | 0 | 46 (0) | 23 | 24k | 2026-06-17 | FIA | Jolpica-F1, OpenF1, NASCAR cacher JSON | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Boxing | tournament_outright | 10 (9) | 389 | 966 (522) | 875 | 228.6M | 2025-05-02 | DAZN, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; needs schedule/bracket simulation |
| Boxing | score_props | 6 (2) | 33 | 179 (102) | 150 | 27.6M | 2025-12-18 | DAZN, ESPN, Fox Sports | ESPN site API, Kalshi live data / game stats | BoxRec blocked; records sparse | verified 2/2 (historical) | C | too few settled events; needs scoring-event timing (PBP) for first-scorer/timing props |
| Boxing | game_winner | 3 (2) | 1 | 3 (1) | 3 | 461k | 2026-02-17 | BBC Sport, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | BoxRec blocked; records sparse | verified 1/1 (historical) | C | too few settled events |
| Boxing | other_novelty | 1 (0) | 0 | 0 (0) | 0 | 0 |  | ESPN, The Wall Street Journal | ESPN site API, Kalshi live data / game stats | - | not sampled | E | not a modelable sporting outcome or derived combo |
| Other | tournament_outright | 23 (5) | 26 | 859 (401) | 428 | 17.2M | 2025-06-18 | ESPN, ESPN, European Union | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; needs schedule/bracket simulation |
| Other | other_novelty | 16 (1) | 75 | 508 (501) | 342 | 13.9M | 2025-06-18 | AP, ESPN, Fanatics Fest | Kalshi live data / game stats, HTML-only results sites | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Other | league_leaders | 2 (1) | 3 | 26 (3) | 23 | 1.0M | 2026-06-28 | ESPN, Fox Sports, Major League Eating | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; inputs need live capture; one outcome per stat-season; needs as-of season stats |
| Other | field_finish | 6 (1) | 28 | 169 (107) | 124 | 698k | 2025-10-01 | AP, ESPN, ESPN | Kalshi live data / game stats, HTML-only results sites | Not assessed beyond Kalshi data | verified 2/2 (historical) | B | few settled events; needs full-field results; tie rules (golf ties count as position) |
| Other | awards | 1 (0) | 1 | 1 (1) | 1 | 14k | 2026-07-14 | ESPN | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 1/1 (historical) | F | few outcomes; inactive; voting outcomes; no free as-of odds/straw-poll history |
| Olympics | field_finish | 33 (0) | 369 | 7,565 (7,518) | 3,128 | 17.9M | 2026-01-27 | the Winter Olympics 2026 | HTML-only results sites, Kalshi live data / game stats | Event-specific; quadrennial sample | verified 2/2 (historical) | B | few settled events; needs full-field results; tie rules (golf ties count as position) |
| Olympics | tournament_outright | 7 (0) | 4 | 116 (116) | 98 | 9.8M | 2026-01-20 | the Winter Olympics 2026 | HTML-only results sites, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few independent outcomes; needs schedule/bracket simulation |
| Table Tennis | game_winner | 10 (0) | 15,209 | 30,432 (5,656) | 24,986 | 19.6M | 2026-01-15 | ESPN, ESPN, Flashscore | Kalshi live data / game stats, HTML-only results sites | Player ratings derivable only from Kalshi-observed results | verified 2/2 (historical+live) | B | independent results source partial |
| Table Tennis | tournament_outright | 2 (0) | 2 | 16 (16) | 16 | 4k | 2026-04-30 | ESPN, Fox Sports, World Table Tennis | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few independent outcomes; needs schedule/bracket simulation |
| Aussie Rules | game_winner | 1 (0) | 224 | 448 (372) | 447 | 7.7M | 2026-03-04 | ESPN, Fox Sports | Squiggle AFL API, AFL Tables, Kalshi live data / game stats | Derivable from results; Squiggle aggregate tips are dated | verified 2/2 (live) | A |  |
| Chess | tournament_outright | 20 (2) | 31 | 751 (249) | 328 | 6.4M | 2025-02-13 | AP, ESPN, Fox Sports | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (live) | C | few independent outcomes; needs schedule/bracket simulation |
| Chess | field_finish | 2 (1) | 54 | 3,379 (1,177) | 1,535 | 401k | 2026-05-20 | Chess.com, Chess.com, Liquipedia | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | Monthly FIDE lists are dated (as-of by month) | verified 2/2 (live) | B | few settled events; needs full-field results; tie rules (golf ties count as position) |
| Chess | game_winner | 3 (1) | 35 | 1,333 (599) | 465 | 332k | 2026-05-15 | Chess.com, Chess.com, FIDE | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | Monthly FIDE lists are dated (as-of by month) | verified 2/2 (historical) | C | too few settled events |
| Chess | rankings_polls | 2 (1) | 19 | 490 (176) | 228 | 247k | 2026-04-23 | FIDE World Top Players | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; inputs need live capture; poll/ranking processes; KenPom is paid |
| Chess | other_novelty | 3 (0) | 12 | 37 (22) | 35 | 53k | 2025-12-09 | Chess.com, ESPN, Fox Sports | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | - | verified 2/2 (historical+live) | E | not a modelable sporting outcome or derived combo |
| Chess | awards | 1 (1) | 0 | 36 (0) | 11 | 5k | 2026-08-11 | Chess.com | FIDE rating lists, Chess.com / Lichess public APIs, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; inputs need live capture; voting outcomes; no free as-of odds/straw-poll history |
| Lacrosse | game_winner | 5 (0) | 501 | 1,002 (976) | 1,002 | 3.9M | 2026-02-03 | ESPN, Fox Sports, Kalshi using information originating from the NCAA | NCAA.com scoreboard JSON, ESPN site API, Kalshi live data / game stats | Derivable from results | verified 2/2 (historical+live) | A |  |
| Lacrosse | tournament_outright | 5 (2) | 3 | 182 (80) | 133 | 1.7M | 2026-01-31 | ESPN, Fox Sports, Fox Sports | NCAA.com scoreboard JSON, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Lacrosse | awards | 1 (0) | 1 | 26 (26) | 23 | 172k | 2026-02-02 | ESPN, Kalshi using information originating from the NCAA | NCAA.com scoreboard JSON, ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; voting outcomes; no free as-of odds/straw-poll history |
| Rugby | game_winner | 7 (2) | 530 | 1,568 (1,174) | 1,384 | 3.0M | 2026-01-22 | ESPN, ESPN, Flashscore | ESPN site API, Kalshi live data / game stats | Derivable from results | verified 2/2 (historical+live) | B | independent results source partial |
| Rugby | tournament_outright | 8 (3) | 9 | 111 (30) | 51 | 58k | 2026-02-17 | AP, ESPN, ESPN | ESPN site API, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical+live) | C | few independent outcomes; needs schedule/bracket simulation |
| Unassigned | other_novelty | 6 (0) | 2 | 64 (50) | 59 | 1.4M | 2025-07-16 | AP, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Unassigned | combo_parlay | 1 (0) | 7 | 36 (36) | 36 | 1.3M | 2025-12-23 | the league governing the game, the league governing the game | Kalshi live data / game stats, HTML-only results sites | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Unassigned | personnel_ops | 3 (0) | 1 | 1 (1) | 1 | 5k | 2025-08-07 | ABC, Fox News, MSNBC | Kalshi live data / game stats, HTML-only results sites | - | verified 1/1 (historical) | E | not a modelable sporting outcome or derived combo |
| Unassigned | awards | 1 (0) | 0 | 0 (0) | 0 | 0 |  | Associated Press, CBS Sports, ESPN | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | not sampled | F | no traded markets; not currently listed; voting outcomes; no free as-of odds/straw-poll history |
| Cycling | tournament_outright | 2 (0) | 5 | 598 (391) | 113 | 1.1M | 2026-05-08 | Cycling Weekly, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few independent outcomes; needs schedule/bracket simulation |
| Cycling | field_finish | 1 (0) | 42 | 7,728 (3,864) | 760 | 1.0M | 2026-07-03 | Cycling Weekly, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | Not assessed beyond Kalshi data | verified 2/2 (historical) | B | few settled events; needs full-field results; tie rules (golf ties count as position) |
| Cycling | awards | 1 (0) | 5 | 920 (368) | 24 | 73k | 2026-07-06 | Cycling Weekly, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; voting outcomes; no free as-of odds/straw-poll history |
| Non-sport (sports-tagged) | other_novelty | 15 (0) | 10 | 106 (106) | 88 | 1.8M | 2025-02-08 | AP, ESPN, ESPN Fantasy | Kalshi live data / game stats, HTML-only results sites | - | verified 2/2 (historical) | E | not a modelable sporting outcome or derived combo |
| Non-sport (sports-tagged) | personnel_ops | 1 (0) | 1 | 1 (1) | 1 | 22k | 2025-09-17 | The Source Agencies are, in hierarchical order, official governing bodies or organizers of  (e | Kalshi live data / game stats, HTML-only results sites | - | verified 1/1 (historical) | E | not a modelable sporting outcome or derived combo |
| Non-sport (sports-tagged) | league_leaders | 2 (2) | 0 | 47 (0) | 46 | 14k | 2026-05-04 | ESPN Fantasy | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | not sampled | E | not a sporting outcome (sports-tagged); one outcome per stat-season; needs as-of season stats |
| Non-sport (sports-tagged) | tournament_outright | 1 (0) | 1 | 33 (33) | 13 | 1k | 2025-09-09 | AP, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | E | not a sporting outcome (sports-tagged); few independent outcomes; needs schedule/bracket simulation |
| Volleyball | game_winner | 2 (1) | 800 | 1,716 (0) | 1,530 | 1.7M | 2026-08-13 | ESPN, Fox Sports, Kalshi using information originating from the NCAA | NCAA.com scoreboard JSON, Kalshi live data / game stats | Derivable from results | verified 2/2 (live) | A |  |
| Volleyball | tournament_outright | 1 (1) | 0 | 49 (0) | 11 | 8k | 2026-09-11 | ESPN, Kalshi using information originating from the NCAA | NCAA.com scoreboard JSON, Kalshi live data / game stats | results-derived only; season/bracket state reconstructable from results | not sampled | C | few independent outcomes; needs schedule/bracket simulation |
| Darts | game_winner | 1 (1) | 616 | 1,374 (424) | 1,196 | 1.2M | 2025-12-11 | Flashscore, Sky Sports, Sofascore | Kalshi live data / game stats, HTML-only results sites | Derivable from results | verified 2/2 (historical) | B | independent results source partial |
| Darts | tournament_outright | 2 (1) | 1 | 26 (8) | 26 | 126k | 2026-02-06 | ESPN, Fox Sports, PDC | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | C | few independent outcomes; needs schedule/bracket simulation |
| Squash | tournament_outright | 2 (0) | 3 | 172 (172) | 35 | 248k | 2026-04-13 | AP, ESPN, Fox Sports | Kalshi live data / game stats, HTML-only results sites | results-derived only; season/bracket state reconstructable from results | verified 2/2 (historical) | F | few outcomes; inactive; few independent outcomes; needs schedule/bracket simulation |
| Squash | game_winner | 1 (0) | 170 | 340 (340) | 223 | 52k | 2026-05-05 | ESPN, Fox Sports, PSA Squash Tour | Kalshi live data / game stats, HTML-only results sites | Not assessed beyond Kalshi data | verified 2/2 (historical) | B | independent results source partial |
| Rowing | field_finish | 1 (0) | 8 | 169 (169) | 11 | 3k | 2025-10-17 | Regatta Central, the Governing League | Kalshi live data / game stats, HTML-only results sites | Not assessed beyond Kalshi data | verified 2/2 (historical) | F | too few settled events; inactive; needs full-field results; tie rules (golf ties count as position) |
<!-- END:matrix -->

### 3.1 Readiness by contract family

<!-- BEGIN:readiness_by_family -->
| Contract family | Cells | A | B | C | D | E | F |
|---|---|---|---|---|---|---|---|
| tournament_outright | 23 | 0 | 2 | 15 | 0 | 1 | 5 |
| game_winner | 18 | 11 | 5 | 2 | 0 | 0 | 0 |
| other_novelty | 14 | 0 | 0 | 0 | 0 | 14 | 0 |
| awards | 14 | 0 | 0 | 9 | 0 | 0 | 5 |
| field_finish | 13 | 5 | 5 | 1 | 0 | 0 | 2 |
| personnel_ops | 11 | 0 | 0 | 0 | 0 | 11 | 0 |
| score_props | 10 | 6 | 2 | 1 | 0 | 0 | 1 |
| player_prop | 9 | 0 | 8 | 0 | 0 | 0 | 1 |
| segment | 8 | 6 | 1 | 0 | 0 | 0 | 1 |
| total | 8 | 6 | 1 | 0 | 0 | 0 | 1 |
| league_leaders | 8 | 0 | 0 | 7 | 0 | 1 | 0 |
| spread | 7 | 5 | 1 | 0 | 0 | 0 | 1 |
| test_placeholder | 6 | 0 | 0 | 0 | 0 | 6 | 0 |
| team_total | 6 | 4 | 2 | 0 | 0 | 0 | 0 |
| season_wins | 6 | 0 | 0 | 5 | 0 | 0 | 1 |
| rankings_polls | 5 | 0 | 0 | 3 | 1 | 0 | 1 |
| combo_parlay | 5 | 0 | 0 | 0 | 0 | 5 | 0 |
| draft | 4 | 0 | 0 | 2 | 0 | 0 | 2 |
| series_playoff | 3 | 0 | 0 | 2 | 0 | 0 | 1 |
| **all** | 178 | 43 | 27 | 47 | 1 | 38 | 22 |
<!-- END:readiness_by_family -->

## 4. Rules and settlement

Representative rule documents are the per-series `contract_terms_url` PDFs on
`assets.kalshi.com/contract_terms/` (771 distinct; 764 returned HTTP 200 on 2026-10-08) plus each
market's `rules_primary` / `rules_secondary` text. Clauses below were extracted from 175 of the
most-used and sport-specific templates (`data/results/sports_contract_terms_extract_v1.json`) and
from market-level rules sampled during the crawl.

### 4.1 Settlement sources

Every series lists `settlement_sources`. Across the sports series: ESPN (2,936 listings),
Fox Sports (1,621), "the Governing League" (864), FIFA (389), then league sites, Flashscore,
Sofascore, Riot Games, HLTV, Chess.com, Sleeper, Baseball-Reference, and KenPom. The settlement
source is usually free to read once the event is final, but **the settlement source is not a
pre-event input source**: ESPN/Fox box scores establish outcomes only.

Two settlement sources create blockers:

- **KenPom** (`KXNCAAMBKENPOM*` rankings markets) settles on a paid subscription rating; the
  outcome itself cannot be verified without paying. Classified D.
- **Baseball-Reference b-WAR** (`KXLEADERMLBWAR`) is free to read but is revised over time; Kalshi
  rules state that revisions after expiration are not considered, so a historical label needs the
  value as of expiration, not today's page.

### 4.2 Common clauses (apply across families)

- **Cancellation / not played.** Most templates: if the event is cancelled or not completed and
  not resumed within a window (typically 48 hours for games, two weeks for tennis/golf
  tournaments), the market settles to the **last fair price** determined by Kalshi, not to No.
  These markets must be excluded from outcome labels (`void_or_fair_price` outcome type in the
  plan) and counted separately.
- **Postponement.** A game postponed but played within the window remains open and settles on the
  played result. Event IDs must therefore key on the *original* scheduled date
  (`sports_phase1_plan_v1.md` section 2.4).
- **Revisions.** "Revisions after expiration will not be considered" appears in stat templates.
  Labels must be frozen at expiration; later stat corrections in free sources are noise relative
  to Kalshi settlement.
- **Early close.** Many markets carry `can_close_early` and `early_close_condition`
  (for example, closes when the outcome is determined). Price histories end early for these.

### 4.3 By contract family

| Family | Key rules (representative templates) | Modelling implication |
|---|---|---|
| game_winner | Basketball: winner of the entire game including overtime ([BASKETBALLGAMEWIN](https://assets.kalshi.com/contract_terms/BASKETBALLGAMEWIN.pdf)). Football: tie resolves to Tie strike where listed, otherwise 50/50; game suspended before 55 minutes and not resumed in 48 h settles to fair price ([FOOTBALLGAMEWIN](https://assets.kalshi.com/contract_terms/FOOTBALLGAMEWIN.pdf)). Baseball: extra innings count; shortened official games count; not resumed in 48 h settles to fair price; an unnecessary series game settles to fair price ([BASEBALLGAMEWIN](https://assets.kalshi.com/contract_terms/BASEBALLGAMEWIN.pdf)). Soccer: result over the scoped period (regulation by default, 90 min plus stoppage), separate Draw market; abandonment before 90 minutes and not resumed settles to fair price ([SOCCERGAMEWIN](https://assets.kalshi.com/contract_terms/SOCCERGAMEWIN.pdf), [SOCCERGAME](https://assets.kalshi.com/contract_terms/SOCCERGAME.pdf)). Tennis: walkover/no ball played settles to fair price; retirement or withdrawal after play starts is No for that player ([TENNISMATCH](https://assets.kalshi.com/contract_terms/TENNISMATCH.pdf)). Cricket: DLS result counts; tie/draw/no-result is not a win ([CRICKETMATCHWIN](https://assets.kalshi.com/contract_terms/CRICKETMATCHWIN.pdf), [CRICKETTESTMATCHWIN](https://assets.kalshi.com/contract_terms/CRICKETTESTMATCHWIN.pdf)). Esports: series winner as defined by the organiser; disqualification before play settles to fair price ([ESPORTSWIN](https://assets.kalshi.com/contract_terms/ESPORTSWIN.pdf)). AFL: tie settles 50/50. | Soccer is three-way (home/draw/away) at 90 minutes; US sports are two-way including OT. Tennis retirement is a real No, not a void. |
| spread | Margin includes OT for basketball/football/hockey; exact tie on a whole-number line resolves on zero differential ([BASKETBALLSPREADS](https://assets.kalshi.com/contract_terms/BASKETBALLSPREADS.pdf), [FOOTBALLSPREAD](https://assets.kalshi.com/contract_terms/FOOTBALLSPREAD.pdf), [HOCKEYSPREADS](https://assets.kalshi.com/contract_terms/HOCKEYSPREADS.pdf), [BASEBALLSPREAD](https://assets.kalshi.com/contract_terms/BASEBALLSPREAD.pdf), [SOCCERSPREAD](https://assets.kalshi.com/contract_terms/SOCCERSPREAD.pdf)). | Ladder of strikes on one margin distribution: strikes in an event are not independent observations. |
| total | Totals include OT; hockey totals include OT and the shootout winning goal ([HOCKEYTOTALS](https://assets.kalshi.com/contract_terms/HOCKEYTOTALS.pdf), [BASKETBALLTOTALS](https://assets.kalshi.com/contract_terms/BASKETBALLTOTALS.pdf), [FOOTBALLTOTALS](https://assets.kalshi.com/contract_terms/FOOTBALLTOTALS.pdf), [BASEBALLTOTALS](https://assets.kalshi.com/contract_terms/BASEBALLTOTALS.pdf), [SOCCERTOTALS](https://assets.kalshi.com/contract_terms/SOCCERTOTALS.pdf)); tennis total games ([TENNISTOTALGAMES](https://assets.kalshi.com/contract_terms/TENNISTOTALGAMES.pdf)); esports total maps ([ESPORTSMAPS](https://assets.kalshi.com/contract_terms/ESPORTSMAPS.pdf)). | Hockey shootout rule differs from many sportsbooks; label from Kalshi definition, not from "final score" columns of third-party data without checking. |
| team_total | Same inclusions as totals ([SOCCERTEAMTOTAL](https://assets.kalshi.com/contract_terms/SOCCERTEAMTOTAL.pdf)). | Shares the score model with spread/total. |
| segment | Quarter/half/period markets **exclude** overtime; first-half soccer markets cover the first half only ([SOCCER1H](https://assets.kalshi.com/contract_terms/SOCCER1H.pdf), [SOCCERHTOTAL](https://assets.kalshi.com/contract_terms/SOCCERHTOTAL.pdf), [HOCKEYWINNINGINPERIOD](https://assets.kalshi.com/contract_terms/HOCKEYWINNINGINPERIOD.pdf)). | Needs period-level scores (play-by-play or line scores), which free sources provide for the major US leagues but not for most soccer leagues beyond half-time scores. |
| score_props | BTTS, exact score, first to score, method/round of finish ([SOCCERBTTS](https://assets.kalshi.com/contract_terms/SOCCERBTTS.pdf), [SOCCEREXACTSCORE](https://assets.kalshi.com/contract_terms/SOCCEREXACTSCORE.pdf), [SOCCERFINISHMETHOD](https://assets.kalshi.com/contract_terms/SOCCERFINISHMETHOD.pdf), [SOCCERADVANCEMETHOD](https://assets.kalshi.com/contract_terms/SOCCERADVANCEMETHOD.pdf)). | Derivable from a joint score distribution for soccer; MMA/boxing method-of-victory needs fight-level data that was blocked (UFCStats, BoxRec). |
| player_prop | Player stat thresholds; soccer goalscorer: an active player who never enters the match settles to fair price as of before the start ([SOCCERENTITYSTAT](https://assets.kalshi.com/contract_terms/SOCCERENTITYSTAT.pdf), [BASKETBALLENTITYSTAT](https://assets.kalshi.com/contract_terms/BASKETBALLENTITYSTAT.pdf), [FOOTBALLENTITYSTAT](https://assets.kalshi.com/contract_terms/FOOTBALLENTITYSTAT.pdf), [BASEBALLENTITYSTAT](https://assets.kalshi.com/contract_terms/BASEBALLENTITYSTAT.pdf), [HOCKEYENTITYSTAT](https://assets.kalshi.com/contract_terms/HOCKEYENTITYSTAT.pdf), [TENNISENTITYSTAT](https://assets.kalshi.com/contract_terms/TENNISENTITYSTAT.pdf), [CRICKETENTITYSTAT](https://assets.kalshi.com/contract_terms/CRICKETENTITYSTAT.pdf)). | Playing time and lineups are the dominant input and are **not** recoverable as of the pre-game time from free sources (injury reports are overwritten). Void-on-DNP rules vary by template and must be read per series. |
| field_finish | Golf: tied positions count as that position; ties at a boundary (for example, Top 10) resolve Yes; withdrawal after tee-off is No, before tee-off fair price; postponement beyond two weeks fair price ([GOLFFINISH](https://assets.kalshi.com/contract_terms/GOLFFINISH.pdf)). F1: official FIA classification; red-flagged race with fewer than two laps fair price; H2H rules for DNF/DSQ ([F1RACE](https://assets.kalshi.com/contract_terms/F1RACE.pdf), [F1H2H](https://assets.kalshi.com/contract_terms/F1H2H.pdf), [F1TOPFINISH](https://assets.kalshi.com/contract_terms/F1TOPFINISH.pdf), [MOTORSPORTTOPFINISH](https://assets.kalshi.com/contract_terms/MOTORSPORTTOPFINISH.pdf)). | Golf "Top N" with ties-resolve-Yes differs from dead-heat sportsbook rules. Many strikes per event (one per competitor) are mutually dependent. |
| tournament_outright | Champion/advance; if the title event is cancelled, settlement splits 1/N among remaining contenders ([TITLE](https://assets.kalshi.com/contract_terms/TITLE.pdf), [SOCCERADVANCE](https://assets.kalshi.com/contract_terms/SOCCERADVANCE.pdf), [TENNISFUTURE](https://assets.kalshi.com/contract_terms/TENNISFUTURE.pdf), [ESPORTSFUTURE](https://assets.kalshi.com/contract_terms/ESPORTSFUTURE.pdf), [NHLPLAYOFF](https://assets.kalshi.com/contract_terms/NHLPLAYOFF.pdf)). | One event per season per competition: few independent observations even with long history. Evaluation through simulated brackets from game-level models, not direct fitting. |
| series_playoff | Series leaders and length ([NBASERIESPOINTSLEADER](https://assets.kalshi.com/contract_terms/NBASERIESPOINTSLEADER.pdf)). | Small samples per year. |
| season_wins | Regular-season wins/exact wins ([NFLWINS](https://assets.kalshi.com/contract_terms/NFLWINS.pdf), [NFLEXACTWINS](https://assets.kalshi.com/contract_terms/NFLEXACTWINS.pdf), [MLBWINS](https://assets.kalshi.com/contract_terms/MLBWINS.pdf)). | One observation per team-season; markets open months before outcomes. |
| league_leaders | Ties for the lead split 1/N ([LEAGUELEADER](https://assets.kalshi.com/contract_terms/LEAGUELEADER.pdf)). b-WAR markets settle on Baseball-Reference. | Needs season-long player projections; few observations. |
| awards | Voting outcomes; co-winners/ties and "No Award Given" handled explicitly ([SPORTAWARD](https://assets.kalshi.com/contract_terms/SPORTAWARD.pdf), [NBAAWARD](https://assets.kalshi.com/contract_terms/NBAAWARD.pdf), [NFLAWARD](https://assets.kalshi.com/contract_terms/NFLAWARD.pdf), [MLBAWARD](https://assets.kalshi.com/contract_terms/MLBAWARD.pdf), [NHLAWARD](https://assets.kalshi.com/contract_terms/NHLAWARD.pdf)). | Driven by voter narrative; no defensible free as-of input set. |
| rankings_polls | AP/CFP polls, KenPom ([RANKLIST](https://assets.kalshi.com/contract_terms/RANKLIST.pdf)). | KenPom paid (D). |
| draft | Pick order ([DRAFTPICK](https://assets.kalshi.com/contract_terms/DRAFTPICK.pdf)). | Annual; information is rumour-driven. |
| personnel_ops | Next coach/team, trades, retirements ([NEXTCOACH](https://assets.kalshi.com/contract_terms/NEXTCOACH.pdf), [NEXTTEAM](https://assets.kalshi.com/contract_terms/NEXTTEAM.pdf), [TRADESPORTS](https://assets.kalshi.com/contract_terms/TRADESPORTS.pdf), [RETIRESPORT](https://assets.kalshi.com/contract_terms/RETIRESPORT.pdf)). | Out of scope (E): no statistical base rate design. |
| combo_parlay | NFL pre-pack: resolves No as soon as any leg fails ([NFLPREPACK](https://assets.kalshi.com/contract_terms/NFLPREPACK.pdf)). | Out of scope (E): requires leg-joint modelling. |
| Athlete participation | "Athlete competes" markets resolve on official participation ([ATHLETEEVENT](https://assets.kalshi.com/contract_terms/ATHLETEEVENT.pdf)). | Classified with personnel_ops. |

Contract-terms links returning 404 on 2026-10-08 are listed in section 1.3; series pointing to them
were checked against their market-level `rules_primary` text instead.

## 5. Data sources

Assessments are reused across categories: each sport points to the same source rows. Status
values: `verified` (data endpoint returned the expected payload), `verified_page` (page answered,
bulk access not exercised), `key_required`, `blocked`, `absent`, `paid`.

<!-- BEGIN:source_table -->
| Source | Status (2026-10-08) | Role | As-of | Depth | Limits | License / terms | Verified URL |
|---|---|---|---|---|---|---|---|
| Kalshi Trade API v2 (public market data) | verified | markets, rules, settlement results, prices | dated | Sports series listed since 2021; most game-level series since 2025 | Unauthenticated reads throttled (HTTP 429 observed above ~8-10 req/s); tiered token budgets documented at docs.kalshi.com/getting_started/rate_limits | Kalshi API/developer terms (not legally reviewed) | <https://docs.kalshi.com/llms.txt> |
| Kalshi candlesticks + trade prints | verified | contemporaneous prices (benchmark, execution) | dated | From market open; 1-min / 60-min / 1-day candles; individual trade prints | No historical order-book depth endpoint; only top-of-book OHLC inside candles | Kalshi API terms | <https://docs.kalshi.com/getting_started/historical_data.md> |
| Kalshi milestones + structured targets | verified | event registry, team/player IDs, external source IDs | none | 154,335 sports milestones (start dates 2025-2027); 280,988 structured targets (all categories) | Paginated (500 / 2000 per page) | Kalshi API terms | <https://docs.kalshi.com/getting_started/targets_and_milestones.md> |
| Kalshi live data / game stats | verified | final scores, winners, method of victory, leaderboards, play-by-play | derived | Milestones from 2025; PBP for NFL, NCAAF, NBA, NCAAB, WNBA, soccer, NHL, MLB | Same Kalshi read budget | Kalshi API terms | <https://docs.kalshi.com/api-reference/live-data/get-game-stats.md> |
| ESPN site API (unofficial JSON) | verified | schedules, results, box scores, leaderboards (multi-sport) | derived | Major US leagues ~2000s+; varies by sport/league | Undocumented; no published quota | Undocumented API under Disney terms of use (disneytermsofuse.com); research caching only | <https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20240115> |
| stats.nba.com / cdn.nba.com | blocked | official NBA/WNBA stats | derived | 1946+ (claimed) | Connection reset (stats) and HTTP 403 (cdn) from this host | NBA terms of use | <https://stats.nba.com/stats/scoreboardv2> |
| Sports Reference (Basketball/Baseball/Pro-Football/Hockey-Reference, FBref) | verified_page | historical stats, awards voting, draft history | derived | Decades | 20 req/min (10 for FBref); FBref returned a Cloudflare challenge (403) | ToU forbids automated access that harms the site and building competing databases | <https://www.sports-reference.com/bot-traffic.html> |
| Barttorvik T-Rank | verified | NCAA basketball team ratings/results | dated | 2008+ (site claim) | Unpublished | Unpublished terms | <https://barttorvik.com/2024_team_results.json> |
| KenPom | paid | NCAA ratings (Kalshi KenPom markets settle on it) | dated | 2002+ | Subscription | Paid | <https://kenpom.com/> |
| EuroLeague live API | verified | EuroLeague results | derived | 2000s+ | Unpublished | Unpublished terms | <https://api-live.euroleague.net/v1/results?seasonCode=E2023> |
| NCAA.com scoreboard JSON (unofficial) | verified | NCAA results (basketball, volleyball, lacrosse, ...) | derived | Several seasons (varies) | Unpublished | NCAA terms (unofficial endpoint) | <https://data.ncaa.com/casablanca/scoreboard/basketball-men/d1/2024/01/20/scoreboard.json> |
| MLB Stats API | verified | MLB schedules, results, box scores, probable pitchers | derived | Verified back to 2005 schedules | Unpublished | MLBAM notice: individual, non-commercial, non-bulk use only (gdx.mlb.com/components/copyright.txt) | <https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2024-07-01> |
| Retrosheet | verified | MLB game logs/event files | derived | 1871+ (released after season) | Bulk files | Free for any use with required attribution notice (retrosheet.org/notice.txt) | <https://www.retrosheet.org/gamelogs/index.html> |
| Baseball Savant (Statcast CSV) | verified | pitch-level MLB data | derived | 2008+ (pitch tracking 2015+) | Query row caps | MLBAM terms | <https://baseballsavant.mlb.com/statcast_search> |
| NPB / KBO official sites | verified_page | NPB/KBO results (HTML) | derived | Multi-season HTML | HTML scraping | Site terms | <https://npb.jp/bis/eng/2024/games/> |
| nflverse (nfldata games.csv, nflverse-data releases) | verified | NFL schedules/results + closing spread/total/moneyline + PBP | closing | 1999-2026 games; lines present through 2026 | GitHub raw/release downloads | nflverse-data repo CC-BY-4.0; nfldata repo has no LICENSE file (GitHub API license=null) | <https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv> |
| CollegeFootballData API | key_required | NCAAF results, historical betting lines | closing | Decades of results; lines ~2013+ (provider claim) | Free key tier quotas | Provider terms (not reviewed) | <https://api.collegefootballdata.com/games?year=2024&week=1> |
| CFL API | blocked | CFL results | derived |  | Key required; connection failed from this host | CFL terms | <https://api.cfl.ca/v1/games/2024> |
| NHL web API (api-web.nhle.com) | verified | NHL schedules, results, boxscores | derived | Verified back to 2010 | Unpublished | NHL terms of service | <https://api-web.nhle.com/v1/score/2024-01-15> |
| MoneyPuck data | verified_page | NHL shot/xG data | derived | 2008+ | Bulk CSV | Terms not verified | <https://moneypuck.com/data.htm> |
| football-data.co.uk | verified | soccer results + pre-closing and closing 1X2, O/U 2.5, Asian handicap odds | closing | 1993+ (EPL); ~22 European divisions + extra leagues (e.g. BRA) | Static CSVs | Free use (site); terms not legally reviewed | <https://www.football-data.co.uk/mmz4281/2324/E0.csv> |
| football-data.org API | key_required | soccer fixtures/results | derived | Free tier: limited competitions | 10 req/min free tier | Provider terms | <https://api.football-data.org/v4/competitions/PL/matches> |
| ClubElo API | blocked | dated club Elo ratings | dated | 1940s+ (site claim) | HTTP 502 on 2026-10-08 (two attempts) | Unpublished | <http://api.clubelo.com/2024-01-15> |
| World Football Elo Ratings | verified | national-team Elo | derived | 1872+ | Static TSV | Unpublished | <https://www.eloratings.net/World.tsv> |
| openfootball football.json | verified | soccer fixtures/results | derived | Multi-season, major leagues | GitHub raw | CC0-1.0 (GitHub API) | <https://raw.githubusercontent.com/openfootball/football.json/master/2023-24/en.1.json> |
| Understat | verified_page | soccer xG (top 6 leagues) | derived | 2014+ | HTML-embedded JSON | Terms not verified | <https://understat.com/league/EPL/2023> |
| Jeff Sackmann tennis_atp / tennis_wta | absent | ATP/WTA match results + rankings | derived |  |  | Was CC BY-NC-SA 4.0 | <https://github.com/JeffSackmann/tennis_atp> |
| tennis-data.co.uk | blocked | ATP/WTA results + closing odds | closing | 2000+ (site claim) | HTTP 403 from this host (default and browser UA) | Unverified | <http://www.tennis-data.co.uk/2024/2024.xlsx> |
| Data Golf API | paid | strokes-gained, predictions, historical odds | dated |  | Paid | Paid | <https://datagolf.com/api-access> |
| Official World Golf Ranking | verified_page | weekly golf rankings | dated | Weekly archive (site) | HTML/JS app | Site terms | <https://www.owgr.com/current-world-ranking> |
| UFCStats | blocked | UFC fight results/stats | derived | 1993+ | Returned a 3 KB 'Loading' JS-challenge page | Site terms | <http://ufcstats.com/statistics/events/completed?page=all> |
| BoxRec | blocked | boxing records | derived |  | Cloudflare 403 | Login-gated; terms restrict scraping | <https://boxrec.com/en/schedule> |
| Jolpica-F1 (Ergast successor) | verified | F1 results, qualifying, standings | derived | 1950+ (1990 verified) | Documented burst/sustained limits | See jolpica-f1 repository | <https://api.jolpi.ca/ergast/f1/2024/results.json> |
| OpenF1 | verified | F1 sessions, laps, timing | derived | 2023+ | Free historical tier | Provider terms | <https://api.openf1.org/v1/sessions?year=2024&session_name=Race> |
| NASCAR cacher JSON (unofficial) | verified | NASCAR schedules/results | derived | Multi-season | Unofficial | NASCAR terms | <https://cf.nascar.com/cacher/2024/1/race_list_basic.json> |
| MotoGP Pulselive API (unofficial) | verified | MotoGP results | derived | 1949+ seasons list | Unofficial | Dorna terms | <https://api.motogp.pulselive.com/motogp/v1/results/seasons> |
| Cricsheet | verified | ball-by-ball cricket results | derived | 2000s+ internationals, IPL, T20 leagues | Bulk zip | ODC-By (JSON/registers); CC-BY 4.0 (XML) | <https://cricsheet.org/downloads/> |
| OpenDota API | verified | Dota 2 pro match results | derived | 2010s+ | Free tier rate limits | Provider terms | <https://api.opendota.com/api/proMatches> |
| Liquipedia MediaWiki/LPDB API | verified | esports results (CS2, LoL, Valorant, Dota, R6, OW) | derived | Multi-year | LPDB requires approval, 60 req/hour; no automated HTML access | CC-BY-SA 3.0 with attribution | <https://liquipedia.net/api-terms-of-use> |
| Oracle's Elixir LoL data | verified_page | LoL pro match data | derived | 2014+ | Google Drive downloads | Site terms | <https://oracleselixir.com/tools/downloads> |
| HLTV | verified_page | CS2 results | derived | 2000s+ | HTML; aggressive anti-bot reported | Site terms | <https://www.hltv.org/results> |
| FIDE rating lists | verified_page | monthly chess ratings | dated | Monthly lists (archive) | Bulk zip | FIDE terms | <https://ratings.fide.com/download_lists.phtml> |
| Chess.com / Lichess public APIs | verified | online ratings/results (Titled Tuesday) | derived | Multi-year | Public API etiquette | Provider terms | <https://api.chess.com/pub/player/hikaru/stats> |
| Squiggle AFL API | verified | AFL results + model tips | dated | 2017+ tips; results longer | User-Agent required | Provider terms | <https://api.squiggle.com.au/?q=games;year=2024;round=1> |
| AFL Tables | verified_page | AFL history | derived | 1897+ | HTML | Site terms | <https://afltables.com/afl/seas/2024.html> |
| HTML-only results sites (dartsdatabase, procyclingstats, WTT, Olympedia, World Athletics, SailGP, NYRR) | verified_page | results | derived | Varies | Scraping required | Per-site terms | <https://www.dartsdatabase.co.uk/> |
| SportsOddsHistory | verified_page | historical futures/award odds tables | dated | Decades (site) | HTML | Site terms | <https://www.sportsoddshistory.com/nba-main/> |
| SportsbookReviewsOnline odds archive | verified_page | historical game odds (NBA/NFL/MLB/NHL/NCAA) | closing | Historic seasons | No downloadable file links found on 2026-10-08 | Site terms | <https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm> |
| TheSportsDB | verified | crowd-sourced schedules/results | derived | Varies | Free test key; paid tiers | Provider terms | <https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d=2024-01-15&s=Soccer> |
<!-- END:source_table -->

### 5.1 Can pre-event inputs be reconstructed as of a past time?

Final results are not evidence that pre-event inputs were available. Each input class was judged
separately (`asof` column above):

- **Derived (genuinely as-of).** Anything computed only from results that finished before the
  forecast time: Elo/rating systems, rolling form, rest days, home/away, head-to-head, season
  standings, bracket state. Reconstructable for every sport whose results source has reliable
  dates and stable team/player identifiers. This is the input set for every A-class cell.
- **Dated (as-of with a timestamp in the source).** Squiggle AFL tips (per-model, dated),
  football-data.co.uk pre-closing odds, nflverse `spread_line`/`total_line` (closing),
  Jolpica F1 qualifying (completed before the race). Usable when the timestamp precedes the Kalshi
  observation time being evaluated; closing lines are benchmarks, not inputs available at an
  earlier Kalshi trade.
- **Overwritten (not as-of).** Injury reports, lineups, probable pitchers, starting goalies,
  weather forecasts, and Kalshi milestone `game_injuries` / `away_last_games` fields are current
  snapshots; historical values are lost. Player-prop and lineup-sensitive models need prospective
  capture of these (category C inputs).
- **Not available free.** Historical odds for NBA, MLB, NHL, college basketball, tennis, golf, MMA,
  boxing, esports, cricket (beyond what the sources above give). ESPN past-game `odds` were absent
  in every sample.

### 5.2 Licensing and rate limits that shape the design

- MLB Stats API (MLBAM notice): individual, non-commercial, non-bulk use. Phase 1 can use it for
  research reads with caching, but a bulk historical backfill should prefer Retrosheet (free with
  attribution notice).
- Sports Reference sites: terms prohibit automated access that harms the site and building
  competing databases; documented limit 20 requests/minute (FBref 10/minute, and FBref answered
  with a Cloudflare challenge). Use for spot verification only.
- Liquipedia: API under CC-BY-SA 3.0 with LPDB access by approval, 60 requests/hour, no automated
  HTML scraping. Too slow for a full esports backfill; OpenDota (free API) covers Dota 2 only.
- nflverse data: CC-BY-4.0 for `nflverse-data` releases; `nfldata` repository has no LICENSE file
  (treat as unlicensed reference, cite and do not redistribute).
- Cricsheet: ODC-By / CC-BY 4.0. openfootball: CC0. Retrosheet: free with notice.
- ESPN site API: undocumented, no published terms for this endpoint family; treat as best-effort
  and cache raw responses.

## 6. Kalshi price history

<!-- BEGIN:price_probe -->
- Probed 309 settled, traded markets across 157 sport x family cells (2026-10-08T23:51:58+00:00); historical cutoff `market_settled_ts` = 2026-08-09T00:00:00Z.
- Hourly candlesticks returned for 308/309 markets (historical tier 206/206, live tier 102/103).
- Of 122,027 hourly candles, 11,429 contain a traded close price and 122,027 a yes-bid close; hours without trades carry quotes only.
- Trade prints returned for 307/309 markets.
- Candle fields observed: `end_period_ts, open_interest, open_interest_fp, price, volume, volume_fp, yes_ask, yes_bid`.
- Trade fields observed: `count_fp, created_time, is_block_trade, no_price_dollars, taker_book_side, taker_outcome_side, taker_side, ticker, trade_id, yes_price_dollars`.
- Markets without candles: `KXKNVBCUPTOTAL-26SEP23GERKOZ-9` (live, empty)
<!-- END:price_probe -->

Method: for every sport x family cell, up to two settled markets with nonzero volume were drawn
from the crawl (`research/sports/price_probe.py`, fixed seed). Each market was fetched from
`/markets/{ticker}` (falling back to `/historical/markets/{ticker}`), then hourly candlesticks over
the market's open-close window (capped at 5,000 hours) and the first page of trades from the
matching tier.

Findings:

- **Price history exists for essentially every traded sports market, on both tiers.** Archived
  markets (settled before 2026-08-09) are served by `/historical/markets/{ticker}/candlesticks`
  and `/historical/trades`; newer ones by the series candlestick endpoint and `/markets/trades`.
- **Most hours have no trade.** Only about 9% of hourly candles carry a traded close; the rest
  carry quotes (yes bid/ask) only. Quote closes are top-of-book snapshots, not depth.
- **Trades are timestamped** (`created_time`) with taker side and price, so a "last trade before
  time T" price is reconstructable. Executable size at T is not (no historical depth).
- Kalshi data starts with the series: the earliest sports markets in this crawl opened
  2024-12-04 (football), and most sports opened during 2025-2026. Kalshi price history is
  therefore at most about two seasons deep, even where free results go back decades.

Consequence for evaluation: forecasting quality can be scored on long free histories, but any
comparison with Kalshi prices is limited to the Kalshi era (2025 onward) and should use trade
prints or quote closes at a fixed pre-event time, with the quadratic fee applied.

## 7. Readiness classes and blockers

Classification is mechanical (`research/sports/report_matrix.py`) so it can be re-run on a new
crawl. "Settled events" is the estimated number of distinct Kalshi events with settled markets.

| Class | Rule | Meaning |
|---|---|---|
| A - historical evaluation ready | Game-level family (winner, spread, total, team total, segment, score props) with at least 200 settled events and a verified free independent results source; field-finish families with at least 30 settled events | A results-derived baseline can be scored chronologically against Kalshi outcomes now. Edge claims additionally need the section 6 price check. |
| B - historical evaluation limited | Game-level families with 50-199 settled events or only a partial independent results source; field-finish with 10-29 events; player props with enough events (inputs not as-of); tournament outrights / series with at least 200 events (heavily correlated within tournaments) | Historical scoring possible but small, correlated, or missing key inputs; results are indicative only. |
| C - prospective collection required | Active or upcoming series that fail the above | Forecasts can only be evaluated by capturing inputs and prices going forward. No collector is registered in this audit. |
| D - blocked by unavailable data | Outcome or required input only from a paid/blocked source (KenPom) | Not pursued under the no-paid-services constraint. |
| E - out of scope for Phase 1 | personnel_ops, other_novelty, combo_parlay, test_placeholder, non-sport series | No defensible statistical design or derived from other contracts. |
| F - no tradable history and inactive | No traded markets and nothing open or upcoming | Nothing to evaluate. |

Cross-cutting blockers:

1. **No historical order books.** Executable-edge evaluation needs contemporaneous asks and depth;
   only trades and candlestick bid/ask closes are historical. Any backtest of edge is an upper
   bound unless it uses trade prints and assumes taker fills at the ask close.
2. **Fees.** Sports series use the quadratic taker fee (3,764 series) or quadratic with maker fees
   (107); 19 series have a 0.5 fee multiplier. `kalshi/fees.py` already supports both types.
3. **Thin markets.** Earlier repo work (`data/results/category_summary.csv`) measured average
   Sports spreads of about 20 cents on its sample and a negative 15.7% ROI with weak evidence.
   Liquidity is concentrated in a few leagues and families (see volumes in the matrix).
4. **Dependence.** Ladders (spreads/totals/props), multi-outcome events (field finishes, outrights),
   and same-game families share one underlying outcome. Effective sample size is the number of
   independent events or event-days, not markets.
5. **Overwritten inputs.** Injuries, lineups, starters, and weather are lost for past events; any
   family whose skill depends on them needs prospective capture.
