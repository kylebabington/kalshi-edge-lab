"""Verify Kalshi price-history availability on sampled settled sports markets.

For each (sport, contract_family) cell, samples up to ``--per-cell`` settled,
traded market tickers recorded by the discovery crawl and checks hourly
candlesticks and trade prints over the market's life. Read-only.

Usage:
    python -m research.sports.price_probe --summary data/results/sports_inventory_summary_v1.json \
        --out data/results/sports_price_probe_v1.json
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import random

from kalshi import cache
from kalshi.client import KalshiAPIError, KalshiClient, KalshiNotFoundError, encode_path_segment


def _ts(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def probe_ticker(client: KalshiClient, ticker: str, series: str, cutoff_ts: int) -> dict:
    row: dict = {"ticker": ticker, "series_ticker": series}
    tier = "live"
    try:
        market = client.get(f"/markets/{encode_path_segment(ticker)}").get("market") or {}
    except KalshiNotFoundError:
        tier = "historical"
        market = client.get(f"/historical/markets/{encode_path_segment(ticker)}").get("market") or {}
    row.update({"tier": tier, "open_time": market.get("open_time"), "close_time": market.get("close_time"),
                "settlement_ts": market.get("settlement_ts"), "result": market.get("result"),
                "volume_fp": market.get("volume_fp")})
    start, end = _ts(market.get("open_time")), _ts(market.get("close_time"))
    if not (start and end):
        row["error"] = "missing open/close"
        return row
    end = min(end, start + 5000 * 3600)
    try:
        if tier == "historical":
            candles = client.get_historical_candlesticks(ticker, start_ts=start, end_ts=end, period_interval=60)
        else:
            candles = client.get_series_market_candlesticks(series, ticker, start_ts=start, end_ts=end,
                                                            period_interval=60)
        with_trade = [c for c in candles if (c.get("price") or {}).get("close_dollars") or
                      (c.get("price") or {}).get("close")]
        with_quote = [c for c in candles if (c.get("yes_bid") or {}).get("close_dollars") or
                      (c.get("yes_bid") or {}).get("close")]
        row.update({"candles": len(candles), "candles_with_trade_close": len(with_trade),
                    "candles_with_bid_close": len(with_quote),
                    "candle_keys": sorted(candles[0].keys()) if candles else []})
    except KalshiAPIError as exc:
        row["candle_error"] = f"{exc.failure_kind}:{exc.status_code}"
    trades_path = "/historical/trades" if tier == "historical" else "/markets/trades"
    try:
        data = client.get(trades_path, params={"ticker": ticker, "limit": 1000})
        trades = data.get("trades") or []
        row.update({"trades_first_page": len(trades), "trades_more_pages": bool(data.get("cursor")),
                    "trade_keys": sorted(trades[0].keys()) if trades else []})
    except KalshiAPIError as exc:
        row["trades_error"] = f"{exc.failure_kind}:{exc.status_code}"
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-cell", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20261008)
    args = ap.parse_args()
    summary = cache.read_json(cache.REPO_ROOT / args.summary)
    client = KalshiClient(min_delay_sec=0.35, progress=print)
    cutoff = client.get_historical_cutoff()
    cutoff_ts = _ts(cutoff.get("market_settled_ts"))
    cells: dict[tuple[str, str], list[tuple[str, str]]] = collections.defaultdict(list)
    for series, info in summary["rules_samples"].items():
        for t in info.get("settled_examples") or []:
            cells[(info["sport"], info["family"])].append((t, series))
    rng = random.Random(args.seed)
    rows = []
    for (sport, family), pool in sorted(cells.items()):
        for ticker, series in rng.sample(pool, min(args.per_cell, len(pool))):
            try:
                row = probe_ticker(client, ticker, series, cutoff_ts)
            except KalshiAPIError as exc:
                row = {"ticker": ticker, "series_ticker": series, "error": f"{exc.failure_kind}:{exc.status_code}"}
            row.update({"sport": sport, "contract_family": family})
            rows.append(row)
            print(sport, family, ticker, row.get("tier"), row.get("candles"), row.get("trades_first_page"))
    cache.write_json(cache.REPO_ROOT / args.out, {
        "probed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "historical_cutoff": cutoff, "rows": rows,
    })


if __name__ == "__main__":
    main()
