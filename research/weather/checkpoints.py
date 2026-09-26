"""Registered operational research checkpoints (America/New_York, DST-safe).

Capture window is frozen at CHECKPOINT_CAPTURE_WINDOW_MINUTES.
Do not widen after observing missed captures.

Scheduler guidance: run --prospective-cycle at HH:05 ET every hour so the
job lands inside [scheduled, scheduled+30m).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kalshi import cache
from research.weather.models import CHECKPOINT_CAPTURE_WINDOW_MINUTES

NYC_TZ = ZoneInfo("America/New_York")

CHECKPOINT_ROOT = cache.REPO_ROOT / "data" / "weather" / "checkpoints"

# Pre-registered Phase 5 checkpoints — do not change times based on later skill.
CHECKPOINT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "checkpoint_id": "dminus1_1800",
        "day_offset": -1,
        "hour": 18,
        "minute": 0,
        "intraday": False,
    },
    {
        "checkpoint_id": "d0_0600",
        "day_offset": 0,
        "hour": 6,
        "minute": 0,
        "intraday": True,
    },
    {
        "checkpoint_id": "d0_0900",
        "day_offset": 0,
        "hour": 9,
        "minute": 0,
        "intraday": True,
    },
    {
        "checkpoint_id": "d0_1200",
        "day_offset": 0,
        "hour": 12,
        "minute": 0,
        "intraday": True,
    },
    {
        "checkpoint_id": "d0_1500",
        "day_offset": 0,
        "hour": 15,
        "minute": 0,
        "intraday": True,
    },
)

CHECKPOINT_IDS: tuple[str, ...] = tuple(s["checkpoint_id"] for s in CHECKPOINT_SPECS)

RECEIPT_STATUS_CAPTURED = "CAPTURED"
RECEIPT_STATUS_MISSED = "MISSED"

SCHEDULER_WINDOWS_TASK_EXAMPLE = {
    "tool": "Windows Task Scheduler",
    "trigger": "Hourly at minute :05 local America/New_York",
    "command": "python weather_model.py --prospective-cycle",
    "rationale": (
        "CHECKPOINT_CAPTURE_WINDOW_MINUTES=30 is frozen. Running at HH:05 places "
        "the job inside [HH:00, HH:30) for each registered checkpoint hour. "
        "Do not widen the window after missed captures."
    ),
    "example": {
        "scheduled_checkpoint": "12:00 ET",
        "task_executes": "~12:05 ET",
        "receipt.scheduled_at": "12:00 ET (checkpoint nominal)",
        "receipt.actual_prediction_as_of": "actual capture UTC timestamp",
    },
}


def ensure_checkpoint_dirs() -> None:
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)


def _parse_target_date(target_date: str | date) -> date:
    if isinstance(target_date, date) and not isinstance(target_date, datetime):
        return target_date
    return datetime.strptime(str(target_date), "%Y-%m-%d").date()


def checkpoint_spec(checkpoint_id: str) -> dict[str, Any]:
    for spec in CHECKPOINT_SPECS:
        if spec["checkpoint_id"] == checkpoint_id:
            return dict(spec)
    raise KeyError(f"Unknown checkpoint_id: {checkpoint_id}")


def is_intraday_checkpoint(checkpoint_id: str) -> bool:
    return bool(checkpoint_spec(checkpoint_id).get("intraday"))


def checkpoint_scheduled_at(
    target_date: str | date,
    checkpoint_id: str,
) -> datetime:
    """Nominal checkpoint instant in America/New_York (DST-aware)."""
    spec = checkpoint_spec(checkpoint_id)
    d0 = _parse_target_date(target_date)
    local_day = d0 + timedelta(days=int(spec["day_offset"]))
    return datetime(
        local_day.year,
        local_day.month,
        local_day.day,
        int(spec["hour"]),
        int(spec["minute"]),
        0,
        0,
        tzinfo=NYC_TZ,
    )


def checkpoint_as_of_utc(target_date: str | date, checkpoint_id: str) -> datetime:
    return checkpoint_scheduled_at(target_date, checkpoint_id).astimezone(timezone.utc)


def capture_window_end(scheduled_at: datetime) -> datetime:
    return scheduled_at + timedelta(minutes=CHECKPOINT_CAPTURE_WINDOW_MINUTES)


def in_capture_window(
    *,
    now: datetime,
    scheduled_at: datetime,
) -> bool:
    """True iff now ∈ [scheduled_at, scheduled_at + WINDOW)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    end = capture_window_end(scheduled_at)
    return scheduled_at <= now < end


def window_elapsed(*, now: datetime, scheduled_at: datetime) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    return now >= capture_window_end(scheduled_at)


@dataclass(frozen=True)
class DueCheckpoint:
    event_ticker: str
    target_date: str
    checkpoint_id: str
    scheduled_at: datetime
    status: str  # "due" | "missed" | "pending" | "future"


def classify_checkpoint(
    *,
    target_date: str,
    checkpoint_id: str,
    now: datetime | None = None,
) -> DueCheckpoint:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
    if in_capture_window(now=now, scheduled_at=scheduled):
        status = "due"
    elif window_elapsed(now=now, scheduled_at=scheduled):
        status = "missed"
    else:
        status = "future"
    return DueCheckpoint(
        event_ticker="",
        target_date=target_date,
        checkpoint_id=checkpoint_id,
        scheduled_at=scheduled,
        status=status,
    )


def receipt_path(event_ticker: str, checkpoint_id: str) -> Path:
    return CHECKPOINT_ROOT / event_ticker / f"{checkpoint_id}.json"


def load_receipt(event_ticker: str, checkpoint_id: str) -> dict[str, Any] | None:
    path = receipt_path(event_ticker, checkpoint_id)
    if not path.exists():
        return None
    data = cache.read_json(path, default=None)
    return data if isinstance(data, dict) else None


def write_receipt(payload: dict[str, Any]) -> Path:
    ensure_checkpoint_dirs()
    event_ticker = str(payload["event_ticker"])
    checkpoint_id = str(payload["checkpoint_id"])
    path = receipt_path(event_ticker, checkpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = cache.read_json(path, default=None)
        if isinstance(existing, dict) and existing.get("status") in {
            RECEIPT_STATUS_CAPTURED,
            RECEIPT_STATUS_MISSED,
        }:
            return path
    cache.write_json(path, payload)
    return path


def list_receipts(
    *,
    event_ticker: str | None = None,
) -> list[dict[str, Any]]:
    ensure_checkpoint_dirs()
    out: list[dict[str, Any]] = []
    root = CHECKPOINT_ROOT
    if event_ticker:
        event_dirs = [root / event_ticker]
    else:
        event_dirs = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    for event_dir in event_dirs:
        if not event_dir.exists():
            continue
        for path in sorted(event_dir.glob("*.json")):
            data = cache.read_json(path, default=None)
            if isinstance(data, dict):
                out.append(data)
    return out


def methodology_checkpoint_block() -> dict[str, Any]:
    return {
        "timezone": "America/New_York",
        "capture_window_minutes": CHECKPOINT_CAPTURE_WINDOW_MINUTES,
        "capture_window_frozen": True,
        "scheduler_minute_et": 5,
        "scheduler": SCHEDULER_WINDOWS_TASK_EXAMPLE,
        "checkpoints": [
            {
                "checkpoint_id": s["checkpoint_id"],
                "day_offset": s["day_offset"],
                "local_time": f"{int(s['hour']):02d}:{int(s['minute']):02d}",
                "intraday": s["intraday"],
            }
            for s in CHECKPOINT_SPECS
        ],
        "note": (
            "Registered before Phase 5 evaluation. Do not change times based on "
            "later skill. DST handled via America/New_York zoneinfo."
        ),
    }
