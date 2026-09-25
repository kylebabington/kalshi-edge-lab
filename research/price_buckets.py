"""Price-bucket definitions and aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


# Continuous coverage: first bucket inclusive on both ends;
# subsequent buckets are (prev_hi, hi].
_SPEC = [
    ("01–05¢", "0.01", "0.05"),
    ("06–10¢", "0.05", "0.10"),
    ("11–15¢", "0.10", "0.15"),
    ("16–20¢", "0.15", "0.20"),
    ("21–25¢", "0.20", "0.25"),
    ("26–30¢", "0.25", "0.30"),
    ("31–35¢", "0.30", "0.35"),
    ("36–40¢", "0.35", "0.40"),
    ("41–45¢", "0.40", "0.45"),
    ("46–50¢", "0.45", "0.50"),
    ("51–55¢", "0.50", "0.55"),
    ("56–60¢", "0.55", "0.60"),
    ("61–65¢", "0.60", "0.65"),
    ("66–70¢", "0.65", "0.70"),
    ("71–75¢", "0.70", "0.75"),
    ("76–80¢", "0.75", "0.80"),
    ("81–85¢", "0.80", "0.85"),
    ("86–90¢", "0.85", "0.90"),
    ("91–95¢", "0.90", "0.95"),
    ("96–99¢", "0.95", "0.99"),
]

BUCKETS = [
    (label, Decimal(lo), Decimal(hi))
    for label, lo, hi in _SPEC
]

DEFAULT_TIGHT_SPREADS = (Decimal("0.10"), Decimal("0.05"))


def price_bucket(price: Decimal | float | str) -> str | None:
    """Assign a dollar price in [0.01, 0.99] to a bucket.

    Boundaries:
      0.05 -> 01–05¢
      0.051 -> 06–10¢
      0.10 -> 06–10¢
      0.101 -> 11–15¢
      0.95 -> 91–95¢
      0.96-0.99 -> 96–99¢
    """
    p = Decimal(str(price))
    if p < Decimal("0.01") or p > Decimal("0.99"):
        return None

    first_label, first_lo, first_hi = BUCKETS[0]
    if first_lo <= p <= first_hi:
        return first_label

    for label, lo, hi in BUCKETS[1:]:
        if lo < p <= hi:
            return label
    return None


@dataclass
class BucketStats:
    label: str
    n: int = 0
    sum_price: Decimal = Decimal("0")
    wins: int = 0
    gross_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    sum_roi: Decimal = Decimal("0")
    capital: Decimal = Decimal("0")

    def add(
        self,
        *,
        price: Decimal,
        won: bool,
        gross: Decimal,
        fee: Decimal,
        net: Decimal,
        roi: Decimal,
        capital: Decimal,
    ) -> None:
        self.n += 1
        self.sum_price += price
        self.wins += int(won)
        self.gross_pnl += gross
        self.fees += fee
        self.net_pnl += net
        self.sum_roi += roi
        self.capital += capital

    def as_executable_dict(self) -> dict:
        avg_entry = (self.sum_price / self.n) if self.n else Decimal("0")
        win_rate = (Decimal(self.wins) / Decimal(self.n)) if self.n else Decimal("0")
        roi = (self.net_pnl / self.capital) if self.capital else Decimal("0")
        return {
            "bucket": self.label,
            "n": self.n,
            "avg_executable_entry": float(avg_entry),
            "win_rate": float(win_rate),
            "entry_gap": float(win_rate - avg_entry),
            "gross_pnl": float(self.gross_pnl),
            "fees": float(self.fees),
            "net_pnl": float(self.net_pnl),
            "roi": float(roi),
        }

    def as_calibration_dict(self) -> dict:
        avg_mid = (self.sum_price / self.n) if self.n else Decimal("0")
        win_rate = (Decimal(self.wins) / Decimal(self.n)) if self.n else Decimal("0")
        roi = (self.net_pnl / self.capital) if self.capital else Decimal("0")
        return {
            "bucket": self.label,
            "n": self.n,
            "avg_midquote": float(avg_mid),
            "win_rate": float(win_rate),
            "calibration_gap": float(win_rate - avg_mid),
            "gross_pnl": float(self.gross_pnl),
            "fees": float(self.fees),
            "net_pnl": float(self.net_pnl),
            "roi": float(roi),
        }

    # Backward-compatible alias used by older tests/callers.
    def as_dict(self) -> dict:
        row = self.as_executable_dict()
        row["avg_entry"] = row["avg_executable_entry"]
        row["implied_win_rate"] = row["avg_executable_entry"]
        row["calibration_gap"] = row["entry_gap"]
        return row


def _aggregate(
    observations: Iterable[dict],
    *,
    side: str,
    price_key: str,
    as_calibration: bool,
) -> list[dict]:
    stats = {label: BucketStats(label=label) for label, _, _ in BUCKETS}
    won_key = "yes_won" if side == "YES" else "no_won"
    fee_key = "yes_fee" if side == "YES" else "no_fee"
    profit_key = "yes_profit" if side == "YES" else "no_profit"
    cost_key = "yes_total_cost" if side == "YES" else "no_total_cost"
    roi_key = "yes_roi" if side == "YES" else "no_roi"

    for obs in observations:
        raw = obs.get(price_key)
        if raw is None or raw == "":
            continue
        price = Decimal(str(raw))
        label = price_bucket(price)
        if label is None:
            continue
        fee = Decimal(str(obs[fee_key]))
        net = Decimal(str(obs[profit_key]))
        capital = Decimal(str(obs[cost_key]))
        gross_profit = net + fee
        stats[label].add(
            price=price,
            won=bool(obs[won_key]),
            gross=gross_profit,
            fee=fee,
            net=net,
            roi=Decimal(str(obs[roi_key])),
            capital=capital,
        )

    if as_calibration:
        return [stats[label].as_calibration_dict() for label, _, _ in BUCKETS]
    return [stats[label].as_executable_dict() for label, _, _ in BUCKETS]


def aggregate_price_buckets(observations: Iterable[dict], *, side: str) -> list[dict]:
    """Executable entry-price results (bucketed by YES/NO taker entry)."""
    entry_key = "yes_entry" if side == "YES" else "no_entry"
    return _aggregate(
        observations, side=side, price_key=entry_key, as_calibration=False
    )


def aggregate_midquote_calibration(
    observations: Iterable[dict], *, side: str = "YES"
) -> list[dict]:
    """Market calibration using midquote as belief proxy (not executable price)."""
    return _aggregate(
        observations, side=side, price_key="midquote", as_calibration=True
    )


def filter_by_max_spread(
    observations: Iterable[dict], max_spread: Decimal | float | str
) -> list[dict]:
    limit = Decimal(str(max_spread))
    return [
        o for o in observations if Decimal(str(o.get("spread", "1"))) <= limit
    ]


def tight_spread_midquote_calibrations(
    observations: Iterable[dict],
    *,
    side: str = "YES",
    spreads: Iterable[Decimal | float | str] = DEFAULT_TIGHT_SPREADS,
) -> list[dict]:
    """Midquote calibration restricted to pre-registered tight-spread subsets."""
    out = []
    for spread in spreads:
        subset = filter_by_max_spread(observations, spread)
        out.append(
            {
                "max_spread": float(Decimal(str(spread))),
                "n": len(subset),
                "buckets": aggregate_midquote_calibration(subset, side=side),
            }
        )
    return out
