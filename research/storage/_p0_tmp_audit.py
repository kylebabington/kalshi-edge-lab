"""One-shot P0 audit of inventory *.tmp candidates. RESEARCH_ONLY / NO_BET."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from research.sports.core import REPO_ROOT

INV = REPO_ROOT / "data" / "cache" / "inventory"
OUT = REPO_ROOT / "data" / "logs" / "storage" / "p0_inventory_tmp_candidates.json"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_tmp_files(root: Path) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return rows
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if ".tmp" not in name.lower():
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError as exc:
                rows.append({"path": str(path), "error": str(exc)})
                continue
            try:
                rel = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
            except ValueError:
                rel = path.as_posix()
            rows.append(
                {
                    "path": rel,
                    "abs_path": str(path.resolve()),
                    "bytes": st.st_size,
                    "mtime_utc": _iso(st.st_mtime),
                    "ctime_utc": _iso(st.st_ctime),
                }
            )
    rows.sort(key=lambda r: int(r.get("bytes") or 0), reverse=True)
    return rows


def _handles_hint(paths: list[str]) -> list[dict]:
    """Best-effort open-handle check via handle.exe or PowerShell (may be empty)."""
    hints: list[dict] = []
    # Try Sysinternals handle if present; otherwise skip.
    for tool in ("handle64.exe", "handle.exe"):
        try:
            proc = subprocess.run(
                [tool, "-accepteula", "-nobanner", "usable_inventory"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.stdout.strip():
                hints.append({"tool": tool, "stdout": proc.stdout[:4000]})
            return hints
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return hints


def _process_scan() -> dict:
    """List python processes that might be writing inventory (names only)."""
    try:
        import psutil  # type: ignore
    except ImportError:
        # Fallback: tasklist
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return {"method": "tasklist", "stdout": proc.stdout[:4000]}
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return {"method": "none", "error": str(exc)}
    rows = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "python" not in name and "pwsh" not in name and "powershell" not in name:
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            if any(
                k in cmd.lower()
                for k in ("historical", "inventory", "usable_inventory", "kalshi", "collector")
            ):
                rows.append(
                    {
                        "pid": p.info["pid"],
                        "name": p.info["name"],
                        "cmdline": cmd[:500],
                    }
                )
        except (psutil.Error, OSError):
            continue
    return {"method": "psutil", "matching_processes": rows}


def main() -> int:
    rows = _find_tmp_files(INV)
    total = sum(int(r.get("bytes") or 0) for r in rows)
    # Final destinations that would have replaced these temps
    finals = {
        "usable_inventory.jsonl": (INV / "usable_inventory.jsonl").exists(),
        "usable_recent.jsonl": (INV / "usable_recent.jsonl").exists(),
    }
    final_sizes = {}
    for name, exists in finals.items():
        p = INV / name
        final_sizes[name] = p.stat().st_size if exists and p.is_file() else None

    # Age gate: refuse to mark as orphan if mtime within last 15 minutes
    now = datetime.now(timezone.utc).timestamp()
    eligible = []
    held_back = []
    for r in rows:
        if "error" in r:
            held_back.append({**r, "reason": "stat_error"})
            continue
        # parse mtime
        mt = datetime.strptime(r["mtime_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        age_s = now - mt
        if age_s < 15 * 60:
            held_back.append({**r, "reason": f"mtime_too_recent_age_s={int(age_s)}"})
            continue
        # prefix patterns from historical.py atomic writers
        base = Path(r["path"]).name
        known = (
            base.startswith(".usable_inventory.")
            or base.startswith(".usable_recent.")
            or ".usable_inventory.merge." in base
            or ".usable_inventory.normalize." in base
            or base.endswith(".jsonl.tmp")
        )
        if not known and not base.endswith(".tmp"):
            held_back.append({**r, "reason": "unrecognized_tmp_name"})
            continue
        eligible.append({**r, "age_days": round(age_s / 86400.0, 2), "classification": "temporary"})

    report = {
        "schema": "kel_p0_inventory_tmp_audit_v1",
        "research_only": True,
        "no_bet": True,
        "assessed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inventory_root": "data/cache/inventory",
        "tmp_file_count": len(rows),
        "tmp_total_bytes": total,
        "tmp_total_gb": round(total / (1024**3), 4),
        "eligible_count": len(eligible),
        "eligible_bytes": sum(int(r["bytes"]) for r in eligible),
        "eligible_gb": round(sum(int(r["bytes"]) for r in eligible) / (1024**3), 4),
        "held_back_count": len(held_back),
        "held_back": held_back,
        "eligible": eligible,
        "final_artifacts": {"exists": finals, "sizes_bytes": final_sizes},
        "process_scan": _process_scan(),
        "handle_hints": _handles_hint([r.get("abs_path", "") for r in eligible[:5]]),
        "deletion_status": "NOT_EXECUTED — awaiting explicit user approval",
        "notes": [
            "Temps are atomic-write leftovers from kalshi.historical usable_inventory merges/normalizes.",
            "Final usable_inventory.jsonl / usable_recent.jsonl are separate files; temps are not evidence.",
            "Do not delete if any held_back reason is mtime_too_recent or an active writer is listed.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "wrote": str(OUT.as_posix()),
        "tmp_total_gb": report["tmp_total_gb"],
        "eligible_gb": report["eligible_gb"],
        "eligible_count": report["eligible_count"],
        "held_back_count": report["held_back_count"],
        "final_artifacts": finals,
        "process_scan_method": report["process_scan"].get("method"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
