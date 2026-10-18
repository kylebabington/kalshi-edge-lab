"""Activity Center record shapes and type constants."""

from __future__ import annotations

from typing import Any, TypedDict

TYPE_FORECAST_ACCEPTED = "forecast_accepted"
TYPE_SETTLEMENT_CONFIRMED = "settlement_confirmed"
TYPE_COLLECTOR_FAILURE = "collector_failure"
TYPE_COLLECTOR_RECOVERY = "collector_recovery"
TYPE_COLLECTOR_SKIPPED = "collector_skipped"
TYPE_BACKUP_FAILURE = "backup_failure"
TYPE_BACKUP_SKIPPED = "backup_skipped"
TYPE_BACKUP_OK = "backup_ok"
TYPE_EXPLORE_DISCOVERED = "explore_discovered"
TYPE_EXPLORE_STATUS_CHANGE = "explore_status_change"
TYPE_EXPLORE_QUOTE_CHANGE = "explore_quote_change"

ACTIVITY_TYPES = (
    TYPE_FORECAST_ACCEPTED,
    TYPE_SETTLEMENT_CONFIRMED,
    TYPE_COLLECTOR_FAILURE,
    TYPE_COLLECTOR_RECOVERY,
    TYPE_COLLECTOR_SKIPPED,
    TYPE_BACKUP_FAILURE,
    TYPE_BACKUP_SKIPPED,
    TYPE_BACKUP_OK,
    TYPE_EXPLORE_DISCOVERED,
    TYPE_EXPLORE_STATUS_CHANGE,
    TYPE_EXPLORE_QUOTE_CHANGE,
)

CATEGORY_WEATHER = "weather"
CATEGORY_SPORTS = "sports"
CATEGORY_KALSHI = "kalshi"
CATEGORY_SYSTEM = "system"

QUOTE_FIELDS = (
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "last_price",
    "yes_bid_size",
    "yes_ask_size",
)


class Subject(TypedDict, total=False):
    kind: str
    key: str
    aliases: list[str]


class ActivityItem(TypedDict, total=False):
    id: str
    type: str
    category: str
    observed_at: str | None
    source_at: str | None
    title: str
    summary: str
    href: str
    subject: Subject
    payload: dict[str, Any]


def item(
    *,
    id: str,
    type: str,
    category: str,
    observed_at: str | None,
    source_at: str | None,
    title: str,
    summary: str,
    href: str,
    subject: Subject | None = None,
    payload: dict[str, Any] | None = None,
) -> ActivityItem:
    return {
        "id": id,
        "type": type,
        "category": category,
        "observed_at": observed_at,
        "source_at": source_at,
        "title": title[:200],
        "summary": summary[:500],
        "href": href,
        "subject": subject or {},
        "payload": payload or {},
    }
