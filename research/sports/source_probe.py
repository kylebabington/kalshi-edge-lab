"""One-shot reachability probe of candidate free sports data sources.

A probe only proves that a URL answered at a given time with a given payload
shape; it does not establish licensing, completeness, or as-of availability.

Usage:
    python -m research.sports.source_probe --out data/results/sports_source_probe.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time

import requests

from kalshi import cache

UA = "kalshi-edge-lab research audit (non-commercial; contact via repo owner)"

# (source_id, role, url). role: data | terms | license | docs
PROBES: list[tuple[str, str, str]] = [
    # --- multi-sport ---
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20240115"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20231015"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard?dates=20240411"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates=20240413"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=20240120"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard?dates=20240120"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard?dates=20240302"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates=20240115"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates=20240701"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=20240120"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates=20231014"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates=20240715"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/rugby/270557/scoreboard?dates=20240120"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/cricket/8048/scoreboard"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/volleyball/womens-college-volleyball/scoreboard?dates=20240915"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/scoreboard?dates=20240420"),
    ("espn_site_api", "data", "https://site.api.espn.com/apis/site/v2/sports/racing/nascar-premier/scoreboard?dates=20240218"),
    ("espn_terms", "terms", "https://disneytermsofuse.com/english/"),
    ("thesportsdb", "data", "https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d=2024-01-15&s=Soccer"),
    ("thesportsdb", "terms", "https://www.thesportsdb.com/docs_api"),
    ("sportsoddshistory", "data", "https://www.sportsoddshistory.com/nba-main/"),
    ("sbr_odds_archive", "data", "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm"),
    # --- basketball ---
    ("nba_cdn", "data", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"),
    ("nba_stats", "data", "https://stats.nba.com/stats/scoreboardv2?GameDate=2024-01-15&LeagueID=00&DayOffset=0"),
    ("nba_terms", "terms", "https://www.nba.com/termsofuse"),
    ("basketball_reference", "data", "https://www.basketball-reference.com/leagues/NBA_2024_games.html"),
    ("sports_reference_terms", "terms", "https://www.sports-reference.com/termsofuse.html"),
    ("sports_reference_bot_policy", "terms", "https://www.sports-reference.com/bot-traffic.html"),
    ("barttorvik", "data", "https://barttorvik.com/2024_team_results.json"),
    ("kenpom", "data", "https://kenpom.com/"),
    ("euroleague_api", "data", "https://api-live.euroleague.net/v1/results?seasonCode=E2023"),
    ("ncaa_casablanca", "data", "https://data.ncaa.com/casablanca/scoreboard/basketball-men/d1/2024/01/20/scoreboard.json"),
    ("ncaa_casablanca", "data", "https://data.ncaa.com/casablanca/scoreboard/volleyball-women/d1/2024/09/15/scoreboard.json"),
    ("ncaa_casablanca", "data", "https://data.ncaa.com/casablanca/scoreboard/lacrosse-men/d1/2024/04/20/scoreboard.json"),
    # --- baseball ---
    ("mlb_statsapi", "data", "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2024-07-01&hydrate=probablePitcher,linescore"),
    ("mlb_copyright", "terms", "https://gdx.mlb.com/components/copyright.txt"),
    ("retrosheet", "data", "https://www.retrosheet.org/gamelogs/index.html"),
    ("retrosheet", "license", "https://www.retrosheet.org/notice.txt"),
    ("baseball_savant", "data", "https://baseballsavant.mlb.com/statcast_search/csv?all=true&type=details&game_date_gt=2024-07-01&game_date_lt=2024-07-01&player_type=pitcher"),
    ("npb_official", "data", "https://npb.jp/bis/eng/2024/games/"),
    ("kbo_official", "data", "https://eng.koreabaseball.com/Schedule/DailySchedule.aspx"),
    # --- football ---
    ("nflverse_games", "data", "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"),
    ("nflverse_games", "license", "https://raw.githubusercontent.com/nflverse/nfldata/master/LICENSE"),
    ("nflverse_data_releases", "data", "https://github.com/nflverse/nflverse-data/releases/tag/pbp"),
    ("cfbd_api", "data", "https://api.collegefootballdata.com/games?year=2024&week=1"),
    ("cfbd_api", "data", "https://api.collegefootballdata.com/lines?year=2024&week=1"),
    ("cfbd_api", "docs", "https://collegefootballdata.com/key"),
    ("cfl_api", "data", "https://api.cfl.ca/v1/games/2024"),
    # --- hockey ---
    ("nhl_api", "data", "https://api-web.nhle.com/v1/score/2024-01-15"),
    ("nhl_api", "data", "https://api-web.nhle.com/v1/club-schedule-season/TOR/20232024"),
    ("nhl_terms", "terms", "https://www.nhl.com/info/terms-of-service"),
    ("moneypuck", "data", "https://moneypuck.com/data.htm"),
    # --- soccer ---
    ("football_data_co_uk", "data", "https://www.football-data.co.uk/mmz4281/2324/E0.csv"),
    ("football_data_co_uk", "docs", "https://www.football-data.co.uk/notes.txt"),
    ("football_data_co_uk", "data", "https://www.football-data.co.uk/new/BRA.csv"),
    ("football_data_org", "data", "https://api.football-data.org/v4/competitions/PL/matches?season=2023"),
    ("clubelo", "data", "http://api.clubelo.com/2024-01-15"),
    ("eloratings_net", "data", "https://www.eloratings.net/World.tsv"),
    ("openfootball", "data", "https://raw.githubusercontent.com/openfootball/football.json/master/2023-24/en.1.json"),
    ("openfootball", "license", "https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md"),
    ("fbref", "data", "https://fbref.com/en/comps/9/2023-2024/schedule/2023-2024-Premier-League-Scores-and-Fixtures"),
    ("understat", "data", "https://understat.com/league/EPL/2023"),
    # --- tennis ---
    ("sackmann_tennis_atp", "data", "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2024.csv"),
    ("sackmann_tennis_atp", "license", "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/README.md"),
    ("sackmann_tennis_wta", "data", "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_2024.csv"),
    ("tennis_data_co_uk", "data", "http://www.tennis-data.co.uk/2024/2024.xlsx"),
    ("tennis_data_co_uk", "docs", "http://www.tennis-data.co.uk/notes.txt"),
    # --- golf ---
    ("datagolf", "docs", "https://datagolf.com/api-access"),
    ("owgr", "data", "https://www.owgr.com/current-world-ranking"),
    ("pga_tour_site", "data", "https://www.pgatour.com/schedule"),
    # --- combat ---
    ("ufcstats", "data", "http://ufcstats.com/statistics/events/completed?page=all"),
    ("boxrec", "data", "https://boxrec.com/en/schedule"),
    ("boxrec", "terms", "https://boxrec.com/en/terms"),
    # --- motorsport ---
    ("jolpica_f1", "data", "https://api.jolpi.ca/ergast/f1/2024/results.json?limit=100"),
    ("jolpica_f1", "docs", "https://github.com/jolpica/jolpica-f1/blob/main/docs/rate_limits.md"),
    ("openf1", "data", "https://api.openf1.org/v1/sessions?year=2024&session_name=Race"),
    ("nascar_cacher", "data", "https://cf.nascar.com/cacher/2024/1/race_list_basic.json"),
    ("motogp_pulselive", "data", "https://api.motogp.pulselive.com/motogp/v1/results/seasons"),
    # --- cricket ---
    ("cricsheet", "data", "https://cricsheet.org/downloads/"),
    ("cricsheet", "license", "https://cricsheet.org/register/"),
    # --- esports ---
    ("opendota", "data", "https://api.opendota.com/api/proMatches"),
    ("liquipedia_mediawiki", "data", "https://liquipedia.net/counterstrike/api.php?action=query&meta=siteinfo&format=json"),
    ("liquipedia_api_terms", "terms", "https://liquipedia.net/api-terms-of-use"),
    ("oracles_elixir", "data", "https://oracleselixir.com/tools/downloads"),
    ("hltv", "data", "https://www.hltv.org/results"),
    ("pandascore", "docs", "https://developers.pandascore.co/docs/introduction"),
    # --- chess ---
    ("fide_ratings", "data", "https://ratings.fide.com/download_lists.phtml"),
    ("chesscom_pubapi", "data", "https://api.chess.com/pub/player/hikaru/stats"),
    ("lichess_api", "data", "https://lichess.org/api/user/DrNykterstein"),
    # --- darts / rugby / other ---
    ("dartsdatabase", "data", "https://www.dartsdatabase.co.uk/"),
    ("squiggle_afl", "data", "https://api.squiggle.com.au/?q=games;year=2024;round=1"),
    ("afltables", "data", "https://afltables.com/afl/seas/2024.html"),
    ("procyclingstats", "data", "https://www.procyclingstats.com/races.php"),
    ("wtt_tabletennis", "data", "https://worldtabletennis.com/"),
    ("olympedia", "data", "https://www.olympedia.org/editions"),
    ("worldathletics", "data", "https://worldathletics.org/records/by-category/world-records"),
    ("sailgp", "data", "https://sailgp.com/races/"),
    ("nyrr_results", "data", "https://results.nyrr.org/"),
]


def probe(url: str, session: requests.Session) -> dict:
    t0 = time.monotonic()
    row: dict = {"url": url, "probed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    try:
        r = session.get(url, timeout=45, headers={"User-Agent": UA}, allow_redirects=True)
        body = r.content[:2_000_000]
        row.update({
            "status": r.status_code,
            "final_url": r.url,
            "content_type": r.headers.get("content-type", ""),
            "bytes": len(r.content),
            "sha256_prefix": hashlib.sha256(r.content).hexdigest()[:16],
            "snippet": body[:400].decode("utf-8", errors="replace"),
        })
    except requests.RequestException as exc:
        row.update({"status": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    row["elapsed_s"] = round(time.monotonic() - t0, 2)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    session = requests.Session()
    rows = []
    for source_id, role, url in PROBES:
        row = probe(url, session)
        row.update({"source_id": source_id, "role": role})
        rows.append(row)
        print(f"{row.get('status')!s:>5} {row.get('bytes', 0):>9} {source_id:24} {url[:100]}")
        time.sleep(0.5)
    cache.write_json(cache.REPO_ROOT / args.out, {"probes": rows})


if __name__ == "__main__":
    main()
