"""Derive activity items from existing saved artifacts. Read-only; no writes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kalshi import cache
from research.activity.types import (
    CATEGORY_SPORTS,
    CATEGORY_SYSTEM,
    CATEGORY_WEATHER,
    TYPE_BACKUP_FAILURE,
    TYPE_BACKUP_OK,
    TYPE_BACKUP_SKIPPED,
    TYPE_COLLECTOR_FAILURE,
    TYPE_COLLECTOR_RECOVERY,
    TYPE_COLLECTOR_SKIPPED,
    TYPE_FORECAST_ACCEPTED,
    TYPE_SETTLEMENT_CONFIRMED,
    ActivityItem,
    item,
)
from research.weather import checkpoints as CK
from research.weather import snapshots as SN

LOG_LINE = re.compile(r"^(\S+) \[pid (\d+)\] cycle (start|end \(exit (-?\d+)\))")
WEATHER_LOG_DIR = cache.REPO_ROOT / "data" / "logs" / "prospective"
SPORTS_CYCLES = cache.REPO_ROOT / "data" / "logs" / "sports" / "collector_cycles.jsonl"
SPORTS_BACKUP_STATUS = cache.REPO_ROOT / "data" / "logs" / "sports" / "backup_status.json"

# Sports backup_status.json is last-run only — never invent intervening failure/recovery history.
BACKUP_FAILURES = frozenset({"failed", "inconsistent_snapshot"})
BACKUP_SKIPPED = frozenset({"skipped_busy", "skipped_overlap", "skipped_not_due", "skipped_paused"})
BACKUP_OK = frozenset({"pushed", "up_to_date", "committed", "ok"})

# Sports collector: skipped/busy is not a failure.
SPORTS_SKIPPED = frozenset({"skipped_lock_busy"})
SPORTS_OK = frozenset({"ok"})


def _iso(ts: Any) -> str | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stat_sig(path: Path) -> tuple[str, int | None, int | None]:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def source_signatures(
    *,
    snapshot_root: Path | None = None,
    score_root: Path | None = None,
    receipt_root: Path | None = None,
    weather_log_dir: Path | None = None,
    sports_capture_dir: Path | None = None,
    sports_score_dir: Path | None = None,
    sports_cycles: Path | None = None,
    sports_backup: Path | None = None,
) -> list[tuple[str, int | None, int | None]]:
    """Filesystem signatures for incremental in-memory rebuilds (never persisted)."""
    from research.sports.live import config as LC
    from research.sports.scoring import config as SCC

    sigs: list[tuple[str, int | None, int | None]] = []
    snap_root = snapshot_root or SN.SNAPSHOT_ROOT
    sc_root = score_root or SN.SCORE_ROOT
    rc_root = receipt_root or CK.CHECKPOINT_ROOT
    log_dir = weather_log_dir or WEATHER_LOG_DIR
    cap_dir = (sports_capture_dir or LC.CAPTURE_DIR) / "accepted" / "checkpoint"
    score_dir = (sports_score_dir or SCC.SCORE_DIR) / "finalized"
    cycles = sports_cycles or SPORTS_CYCLES
    backup = sports_backup or SPORTS_BACKUP_STATUS

    def _dir_sig(root: Path) -> None:
        """Cheap tree signature: top-level child count + max child mtime (no full file walk)."""
        if not root.is_dir():
            sigs.append(_stat_sig(root))
            return
        try:
            children = list(root.iterdir())
        except OSError:
            sigs.append(_stat_sig(root))
            return
        newest = 0
        for child in children:
            try:
                newest = max(newest, child.stat().st_mtime_ns)
            except OSError:
                continue
        sigs.append((str(root), len(children), newest))

    _dir_sig(snap_root)
    _dir_sig(sc_root)
    _dir_sig(rc_root)
    _dir_sig(cap_dir)
    _dir_sig(score_dir)
    if log_dir.is_dir():
        for p in sorted(log_dir.glob("*.log"))[-5:]:
            sigs.append(_stat_sig(p))
    else:
        sigs.append(_stat_sig(log_dir))
    sigs.append(_stat_sig(cycles))
    sigs.append(_stat_sig(backup))
    return sigs


def derive_weather_forecasts(
    *,
    snapshot_root: Path | None = None,
    receipt_root: Path | None = None,
) -> list[ActivityItem]:
    """One item per snapshot capture. Receipts that share snapshot_id are not duplicated."""
    snap_root = snapshot_root or SN.SNAPSHOT_ROOT
    rc_root = receipt_root or CK.CHECKPOINT_ROOT
    out: list[ActivityItem] = []
    seen_snapshots: set[str] = set()

    if snap_root.is_dir():
        for event_dir in sorted(p for p in snap_root.iterdir() if p.is_dir()):
            for path in sorted(event_dir.glob("*.json")):
                if path.name == "latest.json":
                    continue
                snap = _read_json(path)
                if not isinstance(snap, dict):
                    continue
                sid = snap.get("snapshot_id")
                ticker = snap.get("event_ticker")
                if not isinstance(sid, str) or not sid or not isinstance(ticker, str):
                    continue
                if ticker != event_dir.name:
                    continue
                seen_snapshots.add(sid)
                observed = _iso(snap.get("created_at")) or _iso(snap.get("prediction_as_of"))
                source_at = _iso(snap.get("prediction_as_of"))
                out.append(
                    item(
                        id=f"weather.forecast:{sid}",
                        type=TYPE_FORECAST_ACCEPTED,
                        category=CATEGORY_WEATHER,
                        observed_at=observed,
                        source_at=source_at,
                        title=f"Forecast accepted: {ticker}",
                        summary=f"Snapshot {sid}"
                        + (f" at {source_at}" if source_at else ""),
                        href=f"/markets/{ticker}",
                        subject={
                            "kind": "event_ticker",
                            "key": ticker,
                            "aliases": [f"weather:{ticker}", f"kalshi:{ticker}"],
                        },
                        payload={"snapshot_id": sid, "event_ticker": ticker},
                    )
                )

    # CAPTURED receipts without a snapshot_id (or pointing at an unseen snapshot) — rare edge.
    if rc_root.is_dir():
        for event_dir in sorted(p for p in rc_root.iterdir() if p.is_dir()):
            for path in sorted(event_dir.glob("*.json")):
                rec = _read_json(path)
                if not isinstance(rec, dict):
                    continue
                if rec.get("status") != CK.RECEIPT_STATUS_CAPTURED:
                    continue
                ticker = rec.get("event_ticker") or event_dir.name
                if not isinstance(ticker, str):
                    continue
                sid = rec.get("snapshot_id")
                if isinstance(sid, str) and sid and sid in seen_snapshots:
                    continue
                if isinstance(sid, str) and sid:
                    iid = f"weather.forecast:{sid}"
                    if sid in seen_snapshots:
                        continue
                    seen_snapshots.add(sid)
                else:
                    ck = rec.get("checkpoint_id") or path.stem
                    iid = f"weather.forecast.receipt:{ticker}:{ck}"
                observed = _iso(rec.get("created_at")) or _iso(rec.get("actual_prediction_as_of"))
                source_at = _iso(rec.get("actual_prediction_as_of")) or observed
                out.append(
                    item(
                        id=iid,
                        type=TYPE_FORECAST_ACCEPTED,
                        category=CATEGORY_WEATHER,
                        observed_at=observed,
                        source_at=source_at,
                        title=f"Forecast accepted: {ticker}",
                        summary="Checkpoint capture receipt"
                        + (f" ({sid})" if isinstance(sid, str) and sid else ""),
                        href=f"/markets/{ticker}",
                        subject={
                            "kind": "event_ticker",
                            "key": ticker,
                            "aliases": [f"weather:{ticker}", f"kalshi:{ticker}"],
                        },
                        payload={
                            "event_ticker": ticker,
                            "checkpoint_id": rec.get("checkpoint_id"),
                            "snapshot_id": sid,
                        },
                    )
                )
    return out


def _weather_confirmed_settlement(snaps: list[dict], scores: dict[str, dict]) -> dict | None:
    """Mirror weather_index._event_settlement confirmed branch without importing api/."""
    confirmations = []
    for s in snaps:
        sid = s.get("snapshot_id")
        sc = scores.get(sid) if isinstance(sid, str) else None
        if not isinstance(sc, dict):
            continue
        if sc.get("score_status") != SN.SCORE_STATUS_OK:
            continue
        if not sc.get("winning_bucket"):
            continue
        confirmations.append(
            {
                "winning_bucket": sc.get("winning_bucket"),
                "scored_at": sc.get("scored_at"),
                "snapshot_id": sid,
            }
        )
    if not confirmations:
        return None
    buckets = {c["winning_bucket"] for c in confirmations}
    if len(buckets) > 1:
        return None
    first = min(confirmations, key=lambda c: c["scored_at"] or "")
    return {
        "winning_bucket": first["winning_bucket"],
        "confirmed_at": first["scored_at"],
        "confirmed_by_snapshot_id": first["snapshot_id"],
    }


def derive_weather_settlements(*, score_root: Path | None = None, snapshot_root: Path | None = None) -> list[ActivityItem]:
    """Confirmed settlements from scored snapshots (confirmed_at = earliest scored_at)."""
    snap_root = snapshot_root or SN.SNAPSHOT_ROOT
    sc_root = score_root or SN.SCORE_ROOT
    snaps: list[dict] = []
    if snap_root.is_dir():
        for event_dir in sorted(p for p in snap_root.iterdir() if p.is_dir()):
            for path in sorted(event_dir.glob("*.json")):
                if path.name == "latest.json":
                    continue
                snap = _read_json(path)
                if not isinstance(snap, dict):
                    continue
                ticker = snap.get("event_ticker")
                sid = snap.get("snapshot_id")
                if not isinstance(ticker, str) or not isinstance(sid, str):
                    continue
                if ticker != event_dir.name:
                    continue
                snaps.append({"event_ticker": ticker, "snapshot_id": sid})
    scores: dict[str, dict] = {}
    if sc_root.is_dir():
        for path in sorted(sc_root.glob("*.json")):
            sc = _read_json(path)
            if isinstance(sc, dict) and sc.get("snapshot_id"):
                scores[str(sc["snapshot_id"])] = sc
    by: dict[str, list[dict]] = {}
    for s in snaps:
        by.setdefault(s["event_ticker"], []).append(s)
    out: list[ActivityItem] = []
    for ticker, group in by.items():
        settlement = _weather_confirmed_settlement(group, scores)
        if not settlement:
            continue
        confirmed_at = _iso(settlement.get("confirmed_at"))
        if not confirmed_at:
            continue
        out.append(
            item(
                id=f"weather.settlement:{ticker}:{confirmed_at}",
                type=TYPE_SETTLEMENT_CONFIRMED,
                category=CATEGORY_WEATHER,
                observed_at=confirmed_at,
                source_at=confirmed_at,
                title=f"Settlement confirmed: {ticker}",
                summary=f"Winning bucket {settlement.get('winning_bucket')}",
                href=f"/markets/{ticker}",
                subject={
                    "kind": "event_ticker",
                    "key": ticker,
                    "aliases": [f"weather:{ticker}", f"kalshi:{ticker}"],
                },
                payload={
                    "event_ticker": ticker,
                    "winning_bucket": settlement.get("winning_bucket"),
                    "confirmed_by_snapshot_id": settlement.get("confirmed_by_snapshot_id"),
                },
            )
        )
    return out


def _iter_json_files(root: Path, pattern: str):
    if not root.is_dir():
        return
    yield from root.glob(pattern)


# In-memory per-file settlement extract cache (mtime/size keyed). Never written to disk.
_score_ts_cache: dict[str, tuple[int | None, int | None, str | None, str | None, str | None]] = {}


def _score_settlement_ts(path: Path) -> tuple[str | None, str | None, str | None]:
    """Return (event_key, ticker, settlement_ts) for one score file; cached by mtime/size."""
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None, None, None
    cache_key = str(path)
    hit = _score_ts_cache.get(cache_key)
    if hit and hit[0] == sig[0] and hit[1] == sig[1]:
        return hit[2], hit[3], hit[4]
    event_key = None
    ticker = path.stem
    ts = None
    try:
        text = path.read_text(encoding="utf-8")
        if "settlement_ts" in text:
            sc = json.loads(text)
            if isinstance(sc, dict):
                ek = sc.get("event_key")
                if isinstance(ek, str):
                    event_key = ek
                else:
                    cluster = sc.get("cluster")
                    comp = path.parent.parent.name
                    if isinstance(cluster, str):
                        event_key = f"{comp}|{cluster}"
                ts = _iso(((sc.get("settlement") or {}).get("market") or {}).get("settlement_ts"))
                if isinstance(sc.get("ticker"), str):
                    ticker = sc["ticker"]
    except (OSError, ValueError):
        pass
    _score_ts_cache[cache_key] = (sig[0], sig[1], event_key, ticker, ts)
    return event_key, ticker, ts


def derive_sports_forecasts(*, capture_dir: Path | None = None) -> list[ActivityItem]:
    """Light read of WINDOW_CAPTURE records (no checksum — activity identity uses saved fields)."""
    from research.sports.live import config as LC
    from research.sports.service import event_id as sports_event_id

    root = (capture_dir or LC.CAPTURE_DIR) / "accepted" / "checkpoint"
    out: list[ActivityItem] = []
    for path in _iter_json_files(root, "*/*.json"):
        rec = _read_json(path)
        if not isinstance(rec, dict) or rec.get("status") != "WINDOW_CAPTURE":
            continue
        comp, cluster = rec.get("competition"), rec.get("cluster")
        if not isinstance(comp, str) or not isinstance(cluster, str):
            continue
        eid = sports_event_id(comp, cluster)
        as_of = _iso(rec.get("prediction_as_of"))
        if not as_of:
            continue
        observed = _iso(rec.get("finalized_at")) or _iso(rec.get("written_at")) or as_of
        tickers = [t for t in (rec.get("kalshi_event_tickers") or []) if isinstance(t, str)]
        aliases = [f"sports:{eid}", *[f"kalshi:{t}" for t in tickers]]
        out.append(
            item(
                id=f"sports.forecast:{eid}:{as_of}",
                type=TYPE_FORECAST_ACCEPTED,
                category=CATEGORY_SPORTS,
                observed_at=observed,
                source_at=as_of,
                title=f"Forecast accepted: {eid}",
                summary=f"WINDOW_CAPTURE at {as_of}",
                href=f"/sports/events/{eid}",
                subject={"kind": "sports_event_id", "key": eid, "aliases": aliases},
                payload={
                    "event_id": eid,
                    "prediction_as_of": as_of,
                    "kalshi_event_tickers": tickers,
                },
            )
        )
    return out


def derive_sports_settlements(*, capture_dir: Path | None = None, score_dir: Path | None = None) -> list[ActivityItem]:
    """Settled sports events from finalized scores (light parse; no checksum).

    An event is treated as settled for activity when every WINDOW_CAPTURE ok-contract
    has a finalized score *or* — when contract lists are unavailable — when at least
    one score records a settlement_ts for that event_key. Identity uses max settlement_ts.
    """
    from research.sports.live import config as LC
    from research.sports.scoring import config as SCC
    from research.sports.service import event_id as sports_event_id

    cap_root = (capture_dir or LC.CAPTURE_DIR) / "accepted" / "checkpoint"
    sc_root = (score_dir or SCC.SCORE_DIR) / "finalized"

    # event_id -> {tickers, ok_contract_tickers, kalshi_event_tickers}
    captures: dict[str, dict[str, Any]] = {}
    for path in _iter_json_files(cap_root, "*/*.json"):
        rec = _read_json(path)
        if not isinstance(rec, dict) or rec.get("status") != "WINDOW_CAPTURE":
            continue
        comp, cluster = rec.get("competition"), rec.get("cluster")
        if not isinstance(comp, str) or not isinstance(cluster, str):
            continue
        eid = sports_event_id(comp, cluster)
        ok = [
            c.get("ticker")
            for c in (rec.get("contracts") or [])
            if isinstance(c, dict) and c.get("status") == "ok" and isinstance(c.get("ticker"), str)
        ]
        captures[eid] = {
            "ok": ok,
            "kalshi_event_tickers": [t for t in (rec.get("kalshi_event_tickers") or []) if isinstance(t, str)],
        }

    # event_key -> {ticker: settlement_ts}
    settled_ts: dict[str, dict[str, str]] = {}
    for path in _iter_json_files(sc_root, "*/*/*.json"):
        key, tkr, ts = _score_settlement_ts(path)
        if not key or not tkr or not ts:
            continue
        settled_ts.setdefault(key, {})[tkr] = ts

    out: list[ActivityItem] = []
    for eid, meta in captures.items():
        by_ticker = settled_ts.get(eid) or {}
        ok = meta["ok"]
        if ok:
            if not all(t in by_ticker for t in ok):
                continue
            last = max(by_ticker[t] for t in ok)
        else:
            if not by_ticker:
                continue
            last = max(by_ticker.values())
        tickers = meta["kalshi_event_tickers"]
        aliases = [f"sports:{eid}", *[f"kalshi:{t}" for t in tickers]]
        out.append(
            item(
                id=f"sports.settlement:{eid}:{last}",
                type=TYPE_SETTLEMENT_CONFIRMED,
                category=CATEGORY_SPORTS,
                observed_at=last,
                source_at=last,
                title=f"Settlement confirmed: {eid}",
                summary=f"Last settled at {last}",
                href=f"/sports/events/{eid}",
                subject={"kind": "sports_event_id", "key": eid, "aliases": aliases},
                payload={"event_id": eid, "last_settled_at": last, "kalshi_event_tickers": tickers},
            )
        )
    return out


def _collector_transitions(
    cycles: list[dict[str, Any]],
    *,
    domain: str,
    href: str,
    category: str,
    is_failure: Callable[[dict], bool],
    is_ok: Callable[[dict], bool],
    is_skipped: Callable[[dict], bool] | None = None,
    finished_key: str = "finished_at",
) -> list[ActivityItem]:
    """Emit failure / recovery / skipped from an ordered cycle history.

    Recovery requires a prior failure in the same log — absence of failure does not prove recovery.
    """
    out: list[ActivityItem] = []
    saw_failure = False
    for c in cycles:
        finished = _iso(c.get(finished_key))
        if not finished:
            continue
        if is_skipped and is_skipped(c):
            out.append(
                item(
                    id=f"collector.skipped:{domain}:{finished}",
                    type=TYPE_COLLECTOR_SKIPPED,
                    category=category,
                    observed_at=finished,
                    source_at=finished,
                    title=f"{domain.title()} collector skipped",
                    summary=str(c.get("result") or c.get("detail") or "skipped"),
                    href=href,
                    subject={"kind": "collector", "key": domain},
                    payload={"domain": domain, "result": c.get("result"), "detail": c.get("detail")},
                )
            )
            continue
        if is_failure(c):
            saw_failure = True
            detail = c.get("detail") or c.get("result") or f"exit {c.get('exit_code')}"
            out.append(
                item(
                    id=f"collector.failure:{domain}:{finished}",
                    type=TYPE_COLLECTOR_FAILURE,
                    category=category,
                    observed_at=finished,
                    source_at=finished,
                    title=f"{domain.title()} collector failure",
                    summary=str(detail)[:400],
                    href=href,
                    subject={"kind": "collector", "key": domain},
                    payload={
                        "domain": domain,
                        "result": c.get("result"),
                        "exit_code": c.get("exit_code"),
                        "detail": c.get("detail"),
                    },
                )
            )
            continue
        if is_ok(c) and saw_failure:
            out.append(
                item(
                    id=f"collector.recovery:{domain}:{finished}",
                    type=TYPE_COLLECTOR_RECOVERY,
                    category=category,
                    observed_at=finished,
                    source_at=finished,
                    title=f"{domain.title()} collector recovered",
                    summary="First successful cycle after one or more failures.",
                    href=href,
                    subject={"kind": "collector", "key": domain},
                    payload={"domain": domain, "result": c.get("result"), "exit_code": c.get("exit_code")},
                )
            )
            saw_failure = False
    return out


def derive_weather_collector(*, log_dir: Path | None = None) -> list[ActivityItem]:
    directory = log_dir or WEATHER_LOG_DIR
    if not directory.is_dir():
        return []
    cycles: list[dict[str, Any]] = []
    for f in sorted(directory.glob("*.log"))[-14:]:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = LOG_LINE.match(line)
            if not m or m.group(4) is None:
                continue
            finished = _iso(m.group(1))
            if not finished:
                continue
            cycles.append({"finished_at": finished, "exit_code": int(m.group(4))})
    cycles.sort(key=lambda c: c["finished_at"] or "")
    return _collector_transitions(
        cycles,
        domain="weather",
        href="/markets",
        category=CATEGORY_SYSTEM,
        is_failure=lambda c: c.get("exit_code", 0) != 0,
        is_ok=lambda c: c.get("exit_code") == 0,
    )


def derive_sports_collector(*, cycles_path: Path | None = None) -> list[ActivityItem]:
    path = cycles_path or SPORTS_CYCLES
    if not path.is_file():
        return []
    cycles: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            cycles.append(rec)
    cycles.sort(key=lambda c: c.get("finished_at") or c.get("started_at") or "")
    return _collector_transitions(
        cycles,
        domain="sports",
        href="/sports",
        category=CATEGORY_SYSTEM,
        is_failure=lambda c: c.get("result") not in SPORTS_OK | SPORTS_SKIPPED,
        is_ok=lambda c: c.get("result") in SPORTS_OK,
        is_skipped=lambda c: c.get("result") in SPORTS_SKIPPED,
    )


def derive_sports_backup(*, status_path: Path | None = None) -> list[ActivityItem]:
    """Current backup_status.json only — no invented failure/recovery history between reads."""
    path = status_path or SPORTS_BACKUP_STATUS
    st = _read_json(path)
    if not isinstance(st, dict):
        return []
    at = _iso(st.get("finished_at")) or _iso(st.get("at"))
    if not at:
        return []
    outcome = st.get("backup") or st.get("snapshot") or st.get("push")
    if not isinstance(outcome, str):
        return []
    href = "/sports"
    subject = {"kind": "backup", "key": "sports"}
    payload = {
        "backup": st.get("backup"),
        "snapshot": st.get("snapshot"),
        "push": st.get("push"),
        "error": st.get("error"),
        "at": at,
        "history": "current_status_only",
    }
    if outcome in BACKUP_FAILURES or st.get("backup") in BACKUP_FAILURES:
        return [
            item(
                id=f"backup.failure:sports:{at}",
                type=TYPE_BACKUP_FAILURE,
                category=CATEGORY_SYSTEM,
                observed_at=at,
                source_at=at,
                title="Sports backup failure",
                summary=str(st.get("error") or outcome)[:400],
                href=href,
                subject=subject,
                payload=payload,
            )
        ]
    if outcome in BACKUP_SKIPPED or st.get("backup") in BACKUP_SKIPPED or st.get("snapshot") in BACKUP_SKIPPED:
        skipped = st.get("backup") if st.get("backup") in BACKUP_SKIPPED else (
            st.get("snapshot") if st.get("snapshot") in BACKUP_SKIPPED else outcome
        )
        return [
            item(
                id=f"backup.skipped:sports:{at}",
                type=TYPE_BACKUP_SKIPPED,
                category=CATEGORY_SYSTEM,
                observed_at=at,
                source_at=at,
                title="Sports backup skipped",
                summary=str(skipped),
                href=href,
                subject=subject,
                payload=payload,
            )
        ]
    if outcome in BACKUP_OK or st.get("backup") in BACKUP_OK or st.get("push") in BACKUP_OK:
        return [
            item(
                id=f"backup.ok:sports:{at}",
                type=TYPE_BACKUP_OK,
                category=CATEGORY_SYSTEM,
                observed_at=at,
                source_at=at,
                title="Sports backup status",
                summary=f"Current status: {st.get('backup') or outcome}",
                href=href,
                subject=subject,
                payload=payload,
            )
        ]
    # Unknown outcome — surface as current status, not recovery.
    return [
        item(
            id=f"backup.ok:sports:{at}",
            type=TYPE_BACKUP_OK,
            category=CATEGORY_SYSTEM,
            observed_at=at,
            source_at=at,
            title="Sports backup status",
            summary=f"Current status: {outcome}",
            href=href,
            subject=subject,
            payload=payload,
        )
    ]


def derive_all(**path_overrides: Any) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    items.extend(derive_weather_forecasts(
        snapshot_root=path_overrides.get("snapshot_root"),
        receipt_root=path_overrides.get("receipt_root"),
    ))
    items.extend(derive_weather_settlements(
        score_root=path_overrides.get("score_root"),
        snapshot_root=path_overrides.get("snapshot_root"),
    ))
    items.extend(derive_sports_forecasts(capture_dir=path_overrides.get("sports_capture_dir")))
    items.extend(derive_sports_settlements(
        capture_dir=path_overrides.get("sports_capture_dir"),
        score_dir=path_overrides.get("sports_score_dir"),
    ))
    items.extend(derive_weather_collector(log_dir=path_overrides.get("weather_log_dir")))
    items.extend(derive_sports_collector(cycles_path=path_overrides.get("sports_cycles")))
    items.extend(derive_sports_backup(status_path=path_overrides.get("sports_backup")))
    return items
