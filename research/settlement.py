"""Binary settlement validation and taker trade simulation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kalshi.fees import FeeSchedule, compute_taker_fee
from kalshi.historical import is_binary_settlement, settlement_skip_reason


@dataclass(frozen=True)
class TradeResult:
    side: str
    contracts: Decimal
    entry_price: Decimal
    gross_cost: Decimal
    fee: Decimal
    total_cost: Decimal
    payout: Decimal
    net_profit: Decimal
    roi: Decimal
    won: bool


def validate_binary_result(market: dict) -> str | None:
    """Return skip reason or None if market is usable for binary analysis."""
    return settlement_skip_reason(market)


def settlement_payout(result: str, side: str, contracts: Decimal) -> Decimal:
    """
    YES pays $1/contract if result == yes else $0.
    NO pays $1/contract if result == no else $0.

    Never treat arbitrary non-yes values as NO wins — caller must validate.
    """
    if result not in ("yes", "no"):
        raise ValueError(f"Nonstandard settlement result: {result!r}")
    if side == "YES":
        won = result == "yes"
    elif side == "NO":
        won = result == "no"
    else:
        raise ValueError(f"Unknown side: {side}")
    return contracts if won else Decimal("0")


def simulate_taker_trade(
    *,
    side: str,
    entry_price: Decimal,
    result: str,
    contracts: int | Decimal,
    schedule: FeeSchedule,
) -> TradeResult:
    if result not in ("yes", "no"):
        raise ValueError(f"Refuse to simulate nonstandard result={result!r}")
    if not is_binary_settlement({"result": result, "market_type": "binary"}):
        raise ValueError("Non-binary settlement")

    c = Decimal(str(contracts))
    price = Decimal(str(entry_price))
    gross = (price * c)
    fee = compute_taker_fee(price=price, contracts=c, schedule=schedule)
    total = gross + fee
    payout = settlement_payout(result, side, c)
    net = payout - total
    roi = net / total if total != 0 else Decimal("0")
    won = payout > 0

    return TradeResult(
        side=side,
        contracts=c,
        entry_price=price,
        gross_cost=gross,
        fee=fee,
        total_cost=total,
        payout=payout,
        net_profit=net,
        roi=roi,
        won=won,
    )
