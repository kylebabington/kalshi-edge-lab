"""Generate the consolidated coverage matrix and readiness tables for the feasibility report.

Fills ``<!-- BEGIN:name -->`` / ``<!-- END:name -->`` blocks in the target markdown.

Usage:
    python -m research.sports.report_matrix --summary data/results/sports_inventory_summary_v1.json \
        --probe data/results/sports_price_probe_v1.json --doc docs/research/sports_feasibility_v1.md
"""

from __future__ import annotations

import argparse
import collections
import re

from kalshi import cache
from research.sports.source_registry import SOURCES, sport_assessment

GAME_FAMILIES = {"game_winner", "spread", "total", "team_total", "segment", "score_props"}
OUT_OF_SCOPE = {"personnel_ops", "other_novelty", "combo_parlay", "test_placeholder"}
MIN_GAME_EVENTS_READY = 200
MIN_GAME_EVENTS_LIMITED = 50
MIN_FIELD_EVENTS_READY = 30
MIN_FIELD_EVENTS_LIMITED = 10

READINESS = {
    "A": "A - historical evaluation ready",
    "B": "B - historical evaluation limited",
    "C": "C - prospective collection required",
    "D": "D - blocked by unavailable data",
    "E": "E - out of scope (phase 1)",
    "F": "F - no tradable history and not active",
}

FAMILY_BLOCKERS = {
    "game_winner": "",
    "spread": "needs score-margin distribution; OT counts per rules",
    "total": "needs score-total distribution; OT/shootout counts per rules",
    "team_total": "needs per-team scoring distribution",
    "segment": "needs period/half/inning line scores; OT excluded from quarters/halves",
    "score_props": "needs scoring-event timing (PBP) for first-scorer/timing props",
    "player_prop": "player availability/minutes not reconstructable as-of; DNP rules vary",
    "field_finish": "needs full-field results; tie rules (golf ties count as position)",
    "tournament_outright": "few independent outcomes; needs schedule/bracket simulation",
    "series_playoff": "few series per season; needs game model + simulation",
    "season_wins": "one outcome per team-season; needs as-of standings + simulation",
    "awards": "voting outcomes; no free as-of odds/straw-poll history",
    "league_leaders": "one outcome per stat-season; needs as-of season stats",
    "rankings_polls": "poll/ranking processes; KenPom is paid",
    "draft": "mock-draft consensus not archived as-of",
}


def _fmt_int(n: float | int) -> str:
    return f"{int(round(n)):,}"


def _fmt_vol(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:,.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:,.0f}k"
    return f"{v:,.0f}"


def results_independent(sport: str) -> bool:
    status = sport_assessment(sport)["results_status"]
    return status.startswith("free")


def classify_cell(cell: dict, probe_ok: bool | None) -> tuple[str, str]:
    fam, sport = cell["contract_family"], cell["sport"]
    settled = cell.get("settled_events_est", 0)
    active = cell["series_active_or_upcoming"] > 0
    traded = cell["traded_markets"] > 0
    if fam in OUT_OF_SCOPE:
        return "E", "not a modelable sporting outcome or derived combo"
    if cell["sport"].startswith("Non-sport"):
        return "E", "not a sporting outcome (sports-tagged)"
    if fam == "rankings_polls" and any("KENPOM" in t.upper() for t in cell["top_series"]):
        return "D", "settles on paid KenPom ratings"
    if not traded and not active:
        return "F", "no traded markets; not currently listed"
    if fam in GAME_FAMILIES or fam == "player_prop":
        if settled >= MIN_GAME_EVENTS_READY and results_independent(sport):
            if fam == "player_prop":
                return "B", "results-only usage baseline; availability inputs missing"
            return "A", ""
        if settled >= MIN_GAME_EVENTS_LIMITED:
            why = "independent results source partial" if not results_independent(sport) else "small settled sample"
            return "B", why
        return ("C", "too few settled events") if active else ("F", "too few settled events; inactive")
    if fam == "field_finish":
        if settled >= MIN_FIELD_EVENTS_READY and results_independent(sport):
            return "A", ""
        if settled >= MIN_FIELD_EVENTS_LIMITED:
            return "B", "few settled events"
        return ("C", "too few settled events") if active else ("F", "too few settled events; inactive")
    if fam in ("tournament_outright", "series_playoff"):
        if settled >= MIN_GAME_EVENTS_READY and results_independent(sport):
            return "B", "many advance/series events but correlated within tournaments"
        return ("C", "few independent outcomes") if active else ("F", "few outcomes; inactive")
    return ("C", "few independent outcomes; inputs need live capture") if active else (
        "F", "few outcomes; inactive")


def build(summary: dict, probe: dict | None) -> dict[str, str]:
    probe_cells: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in (probe or {}).get("rows", []):
        probe_cells[(row["sport"], row["contract_family"])].append(row)

    def probe_text(sport: str, fam: str) -> tuple[str, bool | None]:
        rows = probe_cells.get((sport, fam))
        if not rows:
            return "not sampled", None
        ok = [r for r in rows if (r.get("candles") or 0) > 0]
        tiers = sorted({r.get("tier", "?") for r in ok})
        if ok:
            return f"verified {len(ok)}/{len(rows)} ({'+'.join(tiers)})", True
        return f"no candles 0/{len(rows)}", False

    cells = summary["cells"]
    lines = ["| Sport | Contract family | Series (active/upcoming) | Settled events (est.) | Markets (archived) "
             "| Traded mkts | Volume | First open | Kalshi settlement sources (top) | Free results sources "
             "| Pre-event inputs (as-of) | Kalshi price history | Readiness | Blockers / notes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    readiness_counts: collections.Counter = collections.Counter()
    readiness_by_family: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    sport_rows: dict[str, dict] = {}
    order = sorted(cells, key=lambda c: (-sum(x["volume_fp"] for x in cells if x["sport"] == c["sport"]),
                                         c["sport"], -c["volume_fp"]))
    for c in order:
        sport, fam = c["sport"], c["contract_family"]
        ptxt, pok = probe_text(sport, fam)
        code, why = classify_cell(c, pok)
        c["readiness"] = code
        readiness_counts[code] += 1
        readiness_by_family[fam][code] += 1
        sa = sport_assessment(sport)
        src = ", ".join(s.split(" <")[0] for s in list(c["settlement_sources"])[:3])
        results = ", ".join(SOURCES[s]["name"].split(" (")[0] for s in sa["results"][:3])
        parts = [p.strip() for x in (why, FAMILY_BLOCKERS.get(fam, "")) for p in x.split(";") if p.strip()]
        blockers = "; ".join(dict.fromkeys(parts))
        inputs = sa["inputs"] if fam in GAME_FAMILIES | {"player_prop", "field_finish"} else \
            "results-derived only; season/bracket state reconstructable from results"
        if fam in OUT_OF_SCOPE:
            inputs = "-"
        lines.append(
            f"| {sport} | {fam} | {c['series']} ({c['series_active_or_upcoming']}) | "
            f"{_fmt_int(c.get('settled_events_est', 0))} | {_fmt_int(c['markets'])} ({_fmt_int(c['hist_markets'])}) | "
            f"{_fmt_int(c['traded_markets'])} | {_fmt_vol(c['volume_fp'])} | {(c['first_open'] or '')[:10]} | "
            f"{src} | {results} | {inputs} | {ptxt} | {code} | {blockers} |")
        s = sport_rows.setdefault(sport, dict(series=0, active=0, settled=0, markets=0, traded=0, volume=0.0,
                                              first=None, families=set(), codes=collections.Counter()))
        s["series"] += c["series"]
        s["active"] += c["series_active_or_upcoming"]
        s["settled"] += c.get("settled_events_est", 0)
        s["markets"] += c["markets"]
        s["traded"] += c["traded_markets"]
        s["volume"] += c["volume_fp"]
        s["first"] = min(filter(None, [s["first"], c["first_open"]]), default=None)
        s["families"].add(fam)
        s["codes"][code] += 1

    sport_lines = ["| Sport | Series | Active/upcoming series | Settled events (est.) | Markets | Traded markets "
                   "| Volume (contracts) | First market open | Families | Cells A/B/C/D/E/F | Free results status |",
                   "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sport, s in sorted(sport_rows.items(), key=lambda kv: -kv[1]["volume"]):
        codes = "/".join(str(s["codes"].get(k, 0)) for k in "ABCDEF")
        sport_lines.append(
            f"| {sport} | {s['series']:,} | {s['active']:,} | {_fmt_int(s['settled'])} | {_fmt_int(s['markets'])} | "
            f"{_fmt_int(s['traded'])} | {_fmt_vol(s['volume'])} | {(s['first'] or '')[:10]} | {len(s['families'])} | "
            f"{codes} | {sport_assessment(sport)['results_status']} |")

    fam_lines = ["| Contract family | Cells | A | B | C | D | E | F |", "|---|---|---|---|---|---|---|---|"]
    for fam, cnt in sorted(readiness_by_family.items(), key=lambda kv: -sum(kv[1].values())):
        fam_lines.append(f"| {fam} | {sum(cnt.values())} | " + " | ".join(str(cnt.get(k, 0)) for k in "ABCDEF") + " |")
    fam_lines.append(f"| **all** | {sum(readiness_counts.values())} | "
                     + " | ".join(str(readiness_counts.get(k, 0)) for k in "ABCDEF") + " |")

    src_lines = ["| Source | Status (2026-10-08) | Role | As-of | Depth | Limits | License / terms | Verified URL |",
                 "|---|---|---|---|---|---|---|---|"]
    for sid, s in SOURCES.items():
        src_lines.append(f"| {s['name']} | {s['status']} | {s['role']} | {s['asof']} | {s['depth']} | {s['limits']} | "
                         f"{s['license']} | <{s['url']}> |")

    probe_lines = []
    if probe:
        rows = probe["rows"]
        ok = [r for r in rows if (r.get("candles") or 0) > 0]
        by_tier = collections.Counter(r.get("tier") for r in rows)
        ok_tier = collections.Counter(r.get("tier") for r in ok)
        tr = [r for r in rows if (r.get("trades_first_page") or 0) > 0]
        probe_lines = [
            f"- Probed {len(rows)} settled, traded markets across {len(probe_cells)} sport x family cells "
            f"({probe['probed_utc']}); historical cutoff `market_settled_ts` = "
            f"{probe['historical_cutoff'].get('market_settled_ts')}.",
            f"- Hourly candlesticks returned for {len(ok)}/{len(rows)} markets "
            f"(historical tier {ok_tier.get('historical', 0)}/{by_tier.get('historical', 0)}, "
            f"live tier {ok_tier.get('live', 0)}/{by_tier.get('live', 0)}).",
            f"- Of {sum(r.get('candles') or 0 for r in ok):,} hourly candles, "
            f"{sum(r.get('candles_with_trade_close') or 0 for r in ok):,} contain a traded close price and "
            f"{sum(r.get('candles_with_bid_close') or 0 for r in ok):,} a yes-bid close; hours without trades "
            f"carry quotes only.",
            f"- Trade prints returned for {len(tr)}/{len(rows)} markets.",
            f"- Candle fields observed: `{', '.join(sorted({k for r in ok for k in r.get('candle_keys', [])}))}`.",
            f"- Trade fields observed: `{', '.join(sorted({k for r in tr for k in r.get('trade_keys', [])}))}`.",
        ]
        bad = [r for r in rows if not (r.get("candles") or 0) > 0]
        if bad:
            probe_lines.append("- Markets without candles: " + ", ".join(
                f"`{r['ticker']}` ({r.get('tier')}, {r.get('candle_error') or r.get('error') or 'empty'})"
                for r in bad[:15]))

    return {"sport_table": "\n".join(sport_lines), "matrix": "\n".join(lines),
            "readiness_by_family": "\n".join(fam_lines), "source_table": "\n".join(src_lines),
            "price_probe": "\n".join(probe_lines)}


def fill(doc_path, blocks: dict[str, str]) -> None:
    text = doc_path.read_text(encoding="utf-8")
    for name, body in blocks.items():
        pattern = re.compile(rf"(<!-- BEGIN:{name} -->\r?\n).*?(<!-- END:{name} -->)", re.S)
        text, n = pattern.subn(lambda m: m.group(1) + body.rstrip("\n") + "\n" + m.group(2), text)
        if n != 1:
            raise SystemExit(f"block {name!r} found {n} times in {doc_path}")
    cache.atomic_write_text(doc_path, text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--probe")
    ap.add_argument("--doc", required=True)
    args = ap.parse_args()
    summary = cache.read_json(cache.REPO_ROOT / args.summary)
    probe = cache.read_json(cache.REPO_ROOT / args.probe) if args.probe else None
    blocks = build(summary, probe)
    fill(cache.REPO_ROOT / args.doc, blocks)
    print({k: v.count("\n") for k, v in blocks.items()})


if __name__ == "__main__":
    main()
