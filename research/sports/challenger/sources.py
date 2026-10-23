"""Bounded, cached public probes of free tennis-result sources. Observed facts only."""

from __future__ import annotations

import re

from ..core import Fetcher, USER_AGENT

PROBES = [
    ("github_jeffsackmann_tennis_atp", "https://api.github.com/repos/JeffSackmann/tennis_atp",
     "GitHub API repository metadata"),
    ("github_jeffsackmann_tennis_wta", "https://api.github.com/repos/JeffSackmann/tennis_wta",
     "GitHub API repository metadata"),
    ("github_jeffsackmann_search_itf",
     "https://api.github.com/search/repositories?q=tennis_itf+in:name+user:JeffSackmann",
     "GitHub search for an ITF repo under JeffSackmann"),
    ("sackmann_atp_matches_2024_raw",
     "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2024.csv",
     "Raw ATP match CSV (ITF WTT coverage is not assumed)"),
    ("espn_tennis_itf_scoreboard",
     "https://site.api.espn.com/apis/site/v2/sports/tennis/itf/scoreboard?dates=20261014",
     "ESPN site API tennis/itf scoreboard for one ITF date"),
    ("espn_tennis_atp_scoreboard",
     "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard?dates=20261014",
     "ESPN site API tennis/atp scoreboard for the same date"),
    ("espn_tennis_wta_scoreboard",
     "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard?dates=20261014",
     "ESPN site API tennis/wta scoreboard for the same date"),
    ("espn_tennis_challenger_scoreboard",
     "https://site.api.espn.com/apis/site/v2/sports/tennis/challenger/scoreboard?dates=20261014",
     "ESPN site API tennis/challenger scoreboard for the same date"),
    ("itftennis_mens_calendar",
     "https://www.itftennis.com/en/tournament-calendar/mens-world-tennis-tour-calendar/",
     "ITF official men's World Tennis Tour calendar page"),
    ("itftennis_rankings_men",
     "https://www.itftennis.com/en/world-tennis-tour/rankings/mens-world-tennis-tour-rankings/",
     "ITF official men's WTT rankings page"),
    ("tennis_data_co_uk_2024",
     "http://www.tennis-data.co.uk/2024/2024.xlsx",
     "tennis-data.co.uk ATP/WTA workbook (ITF presence is observed, not assumed)"),
    ("tennis_data_co_uk_notes",
     "http://www.tennis-data.co.uk/notes.txt",
     "tennis-data.co.uk notes file"),
]

PLAYER_ID_RE = re.compile(r"player[_-]?id|competitor[_-]?id|athleteid|\"id\":\s*\"?\d+", re.I)
SURFACE_RE = re.compile(r"\b(clay|hard|grass|carpet|surface|courtType|court_type)\b", re.I)
ITF_RE = re.compile(r"\bitf\b|world tennis tour|wtt", re.I)


def _observe(body: bytes, content_type: str) -> dict:
    text = body[:200_000].decode("utf-8", errors="replace")
    return {
        "observed_player_id_token": bool(PLAYER_ID_RE.search(text)),
        "observed_surface_token": bool(SURFACE_RE.search(text)),
        "observed_itf_token": bool(ITF_RE.search(text)),
        "looks_like_json": content_type.lower().find("json") >= 0 or text.lstrip()[:1] in "{[",
        "looks_like_html": "text/html" in content_type.lower() or text.lstrip().lower().startswith("<!"),
        "snippet": text[:240].replace("\n", " "),
    }


def probe(fetcher: Fetcher | None = None) -> list[dict]:
    """At most one GET per URL. Cached byte-exact. No paid endpoints."""
    f = fetcher or Fetcher(min_interval=0.5, timeout=45.0)
    rows = []
    for source_id, url, role in PROBES:
        raw = f.get("itf_probe", url, None, cache_statuses=(200, 301, 302, 403, 404, 400, 401, 429, 500, 502, 503),
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
        row = {
            "source_id": source_id, "url": url, "role": role,
            "http_status": raw.status, "bytes": len(raw.body),
            "content_type": (raw.meta or {}).get("content_type") or "",
            "retrieved_at": (raw.meta or {}).get("retrieved_at"),
            "cache_sha256": (raw.meta or {}).get("sha256"),
        }
        if raw.status == -1:
            row["error"] = raw.body.decode("utf-8", errors="replace")[:200]
            row.update({k: None for k in (
                "observed_player_id_token", "observed_surface_token", "observed_itf_token",
                "looks_like_json", "looks_like_html", "snippet")})
        else:
            row.update(_observe(raw.body, row["content_type"]))
        rows.append(row)
    return rows


def usable_itf_results(rows: list[dict]) -> dict:
    """Usable means HTTP 200, not HTML-only, and observed ITF plus player-id tokens. No assumed answers."""
    usable = []
    for r in rows:
        if r.get("http_status") != 200:
            continue
        if r.get("looks_like_html") and not r.get("looks_like_json"):
            continue
        if r.get("observed_itf_token") and r.get("observed_player_id_token"):
            usable.append(r["source_id"])
    return {
        "usable_itf_result_feeds": usable,
        "n_probes": len(rows),
        "n_http_200": sum(1 for r in rows if r.get("http_status") == 200),
    }
