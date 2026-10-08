"""Map Kalshi sports series metadata to sport and contract-family labels.

Kalshi's ``product_metadata.scope`` is the primary signal; series without a
scope fall back to title/ticker patterns. Labels are research groupings, not
Kalshi taxonomy, and every row keeps the raw scope for auditability.
"""

from __future__ import annotations

import re

# Ordered (family, subfamily, pattern) rules matched against "scope | title | ticker".
# First match wins, so more specific patterns precede generic ones.
_RULES: list[tuple[str, str, str]] = [
    ("test_placeholder", "test", r"^\s*\|?\s*(test|delete|xyz|testing)\b|\| (test|delete|xyz|testing) \||testimage"),
    ("combo_parlay", "combo", r"\bcombos?\b|\bparlay|\bsgp\b|^KXMVE|\bMVE\b|pre ?pack"),
    ("other_novelty", "novelty", r"honey deuce|hot dogs eaten|viewership|attendance|badge|guinness|mr ?beast"
                                 r"|endorsement|brand deal|100 mile|road to scratch|wsop|alpha arena"),
    ("personnel_ops", "transactions", r"\btrades?\b|team sale|\bsale\b|acquire|\bjoin|\bsign\b|transfer"
                                      r"|player option|next .*gm\b|owning team|\bdeal\b"),
    ("field_finish", "event_winner", r"olympics? (?!.*medal)|pole position|homerun derby|home run derby"
                                     r"|mr\. olympia"),
    # segment markets (period / half / quarter / inning / set / map)
    ("segment", "winner", r"(1st|2nd|3rd|4th|first|second) (half|quarter|period)[^|]*(winner|moneyline)"
                          r"|quarter winner|period winner|ot winner|first [357] innings winner|inning winner"
                          r"|set \d game winner|set winner|map winner|^map\b|day winner|first round player"),
    ("segment", "spread", r"(half|quarter|period|innings?|set) spread|first 5 innings spread"),
    ("segment", "total", r"(half|quarter|period|innings?) (team )?total|inning total|first 5 innings total"
                         r"|highest scoring quarter|1st half btts|2nd half btts|1st half correct score"),
    # discrete game-outcome props derived from a score distribution
    ("score_props", "btts_correct_score", r"\bbtts\b|both teams to score|correct score|exact match score"
                                          r"|first half / fulltime|winning margin|race to points"),
    ("score_props", "first_to_score", r"first team to score|team to score first|first goal\b(?! ?scorer)"
                                      r"|first goal \+ win|first point|goal in first 10|next touchdown"
                                      r"|first team to score a td"),
    ("score_props", "game_event", r"\bovertime\b|extra innings|yrfi|nrfi|tiebreak|2-point conversion|\btie\b"
                                  r"|weather delay|game delay|\bcorners\b|card props|total sixes|total fours"
                                  r"|number of 6|safety|go the distance|method of (victory|finish)"
                                  r"|round of (finish|victory)|first minute finish|knockout\b"),
    # player props (single game)
    ("player_prop", "player_game_stat",
     r"goalscorer|player (points|goals|assists|rebounds|threes|steals|blocks|free throws)|\btouchdowns?\b"
     r"|receiving|rushing|passing|\bassists\b|\bhits\b|strikeouts|home ?runs|\bshots\b|saves\b|rbis"
     r"|total bases|stolen bases|earned runs|hits allowed|walks|outs recorded|pts \+ reb|double double"
     r"|triple double|steals \+ blocks|three pointers|score or assist|\bbrace\b|field goals|aces\b"
     r"|h2h player|h2h pts|goalkeeper saves|shots on goal|stat escalator|stat ladder|player props"
     r"|hits \+ runs|rushing \+ receiving|\bdefense\b|d/st|starting pitcher|longest (hr|home run|field goal)"
     r"|highest exit velocity|500\+ foot|\beagles\b|hole in one|hole scores|golfer score|round scores"
     r"|golfer o/u|fastest lap|honey deuce|hot dogs eaten|bench points|team total yards|team 1st downs"
     r"|total touchdowns|team touchdowns|squad goals|runs scored"),
    ("team_total", "team_total", r"team total|team points"),
    ("spread", "spread", r"\bspread\b|game spread|series game spread|set spread"),
    ("total", "total", r"point total|game total|total runs|run total|total maps|total games|total sets"
                       r"|totals?\b(?!.*(medals|viewership))|over/under"),
    # playoff series and tournament progression
    ("series_playoff", "series", r"series (winner|exact score|total games|specials|props|leaders|player props"
                                 r"|goal leader)|^series$"),
    ("field_finish", "placement", r"finishing position|top finishers|make the cut|end of round leader"
                                  r"|round \d (leader|finishing position)|3-ball|5-ball|golfer groups"
                                  r"|matchups|head-to-head|head to head|3-hole|winner without|top constructor"
                                  r"|top manufacturer|top team|biggest mover|stage of elimination|round of elimination"
                                  r"|furthest (advancing|stage)|golf specials|driver specials|\brace\b"),
    ("season_wins", "wins", r"\bwins\b|win totals?|h2h win total|season records|streaks?\b|win streak|at \.500"
                            r"|exact wins|reg season futures|playoff seeds|tournament seeds|to finish bottom"
                            r"|regular season"),
    ("awards", "awards", r"award|\bmvp\b|rookie of the year|cy young|all[- ]star|pro bowl|hall of fame"
                         r"|ballon d|heisman|finalists|player of the year|coach of the year"
                         r"|rookie of the (y ?ear|month)|team of the year"),
    ("league_leaders", "season_stat", r"league leaders?|stat leaders|season stats|season averages|career stats"
                                      r"|fantasy|points leader|season milestones|break record|season specials"
                                      r"|playoff leader|weekly stat|conference stat|records\b"),
    ("rankings_polls", "poll", r"ap poll|cfp poll|kenpom|rankings?\b|ratings\b|top 100 list|college gameday"),
    ("draft", "draft", r"\bdraft|combine|drafted by|top pick|first pick"),
    ("personnel_ops", "personnel", r"coach|manager|next (team|club|conference|contract)|retire|transaction"
                                   r"|contracts?\b|franchise expansion|realignment|debut|starting lineups"
                                   r"|squad selection|sanctions|\bhost\b|stadium|schedule|game location"
                                   r"|new team date|to compete|players to compete|\bout\b|ban\b|leave\b"
                                   r"|starters|play in game|to play\b|nationality|viewership|relocat"
                                   r"|week 1 qb|to pitch|next play|occurr?ence|delay|raise|partnership"
                                   r"|ownership|white house|play again|play together|team from|grand prix$"),
    ("tournament_outright", "advance", r"to advance|tournament advancement|qualif|group (winners|exact order"
                                       r"|stage)|group [a-l]\b|round of \d+|knockout stage|pool (winner|qualifiers)|relegation|promotion"
                                       r"|playoff|regions|final matchup|championship matchup|cfp appear"),
    ("tournament_outright", "champion", r"future|champion|winner|cup\b|title|trophies|trebles|conference"
                                        r"|division|tournament|world series|super bowl|stanley|finals"
                                        r"|mid-season cup|open\b|masters|grand slam|majors|medals|city titles"
                                        r"|\b(atp|wta)\b|reach round|league$"),
    ("game_winner", "moneyline", r"^game$|game winner|^match$|\bgame\b|\bmatch\b|\bfight\b|\bbout\b"
                                 r"|\bvs\.?\b|winner"),
]
_COMPILED = [(f, s, re.compile(p, re.I)) for f, s, p in _RULES]

# Families that resolve from a single game/match outcome (shared score model).
GAME_LEVEL = {"game_winner", "spread", "total", "team_total", "segment", "score_props", "player_prop"}


def classify_family(scope: str, title: str, ticker: str) -> tuple[str, str]:
    scope = (scope or "").strip()
    if scope.lower() in {"game", "game winner", "match"}:
        if re.search(r"champion|pole position|derby|world series|tournament winner", title, re.I):
            return classify_family("", title, ticker)
        return "game_winner", "moneyline"
    if scope.lower() in {"future", "futures"}:
        return "tournament_outright", "champion"
    if scope.lower() == "events":
        text = f"{title} | {ticker}"
    else:
        text = f"{scope} | {title} | {ticker}" if scope else f"{title} | {ticker}"
    for family, sub, rx in _COMPILED:
        if rx.search(text):
            return family, sub
    return "other_novelty", "unclassified"


_KEYWORD_SPORT = [
    (r"RANKLIST|EUROVISION|ALPHA ?ARENA|SPELLING ?BEE|SYNTHETIX|KXSBMENTION", "Non-sport (sports-tagged)"),
    (r"DJOKOVIC|DJKOVOIC|SINNER|ALCARAZ", "Tennis"),
    (r"MESSI|KXENGCS", "Soccer"),
    (r"PRESIDENTS ?CUP", "Golf"),
    (r"HORNETS", "Basketball"),
    (r"BELICHICK|KXCOACHOUTAUB", "Football"),
    (r"SKENES", "Baseball"),
    (r"KXEBBALL|KXEFOOTBALL|\bEBASKETBALL\b|\bEFOOTBALL\b", "Esports"),
    (r"REAL ?MADRID|\bBARCA\b|BARCELONA|MANCHESTER CITY|KXMANCITY|BALLON|KXLIGAMX|KXEFL|FRIENDLY GOAL", "Soccer"),
    (r"KNICKS|KXNYK|WESTBROOK|KXLBJ|FREE THROWS", "Basketball"),
    (r"MAHOMES|KXSWIFT", "Football"),
    (r"JON JONES|FIGHT NIGHT", "MMA"),
    (r"KXOLDESTCUT", "Golf"),
    (r"PADEL|KXPPL|PICKLE ?BALL|MARATHON|\bMILE\b|SURFING|SUMO|CLIMBING|FRISBEE|SAILGP|TALL SHIP|WSOP|GUINNESS|HONEY DEUCE|FANATICS|BEAST GAMES|HOT DOG|ESPYS?", "Other"),
]

_TICKER_SPORT = [
    (r"NBA|WNBA|NCAAMB|NCAAWB|NCAAB|EUROLEAGUE|BASKET|NBL|ACB|FIBA", "Basketball"),
    (r"NFL|NCAAF|CFL|CFB|HEISMAN|SUPERBOWL", "Football"),
    (r"MLB|NPB|KBO|WBC|BASEBALL", "Baseball"),
    (r"NHL|KHL|AHL|SHL|HOCKEY", "Hockey"),
    (r"ATP|WTA|TENNIS|USOPEN|WIMBLEDON|FRENCHOPEN|AUSOPEN", "Tennis"),
    (r"PGA|LIV|GOLF|RYDER|MASTERS", "Golf"),
    (r"F1|NASCAR|MOTOGP|INDY", "Motorsport"),
    (r"UFC|MMA|PFL", "MMA"),
    (r"BOX", "Boxing"),
    (r"FIFA|UEFA|EPL|LALIGA|SERIEA|BUNDES|LIGUE|MLS|SOCCER|WC|COPA|CONCACAF|CONMEBOL", "Soccer"),
    (r"CHESS|FIDE", "Chess"),
    (r"CS2|LOL|DOTA|VALORANT|OVERWATCH|ESPORT", "Esports"),
    (r"CRICKET|IPL|T20|ODI", "Cricket"),
]


def classify_sport(tags: list[str] | None, competitions: dict[str, int] | None,
                   competition_to_sport: dict[str, str], ticker: str, title: str) -> tuple[str, str]:
    """Return (sport, basis). Prefers Kalshi tags, then event competition, then ticker."""
    if re.match(r"KXAFL(?!C)", ticker.upper()):
        return "Aussie Rules", "ticker_override"
    tags = [t for t in (tags or []) if t]
    ignore = {"4th of July", "Cities", "International", "Crypto", "Sui", "ESPYS", "Video games"}
    primary = [t for t in tags if t not in ignore]
    if primary:
        sport = primary[0]
        if sport == "UFC":
            sport = "MMA"
        if sport == "CFB":
            sport = "Football"
        return sport, "kalshi_tag"
    for comp, _n in sorted((competitions or {}).items(), key=lambda kv: -kv[1]):
        if comp in competition_to_sport and competition_to_sport[comp] not in ignore:
            return competition_to_sport[comp], "event_competition"
    text = f"{ticker} | {title}".upper()
    for pattern, sport in _KEYWORD_SPORT:
        if re.search(pattern, text):
            return sport, "ticker_title_keyword"
    for pattern, sport in _TICKER_SPORT:
        if re.search(pattern, ticker.upper()):
            return sport, "ticker_pattern"
    if tags:
        secondary = {"Video games": "Esports", "4th of July": "Other", "ESPYS": "Other", "Cities": "Other"}
        return secondary.get(tags[0], "Other"), "kalshi_tag_secondary"
    return "Unassigned", "none"
