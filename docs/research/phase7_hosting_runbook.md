# Phase 7 collector hosting runbook ($0: Oracle Always Free)

Status: prepared, not deployed. Nothing in this runbook has been provisioned,
no external service exists yet, and the Windows task
`KalshiEdgeLab-WeatherProspective` remains the only collector.

Operational code only. The registered protocol, frozen pool, source CSVs and
method are unchanged (`python -m research.weather.collector_ops verify-pins`):

| Pin | SHA-256 |
| --- | --- |
| protocol file | `cc33e71db90d0d6c069791892872f8e74d6d9dc7453e6fa5b9e1b3e79f948ae2` |
| frozen pool | `eb4b6e716c8b5d24ece9a2597d22a2876175648f2e5584adefe86b71b633c4bb` |
| method fingerprint | `95b0d991f47f97068b291b93a953a8f6a4eb84099d9288fd2ae6770228e78e6d` |
| `gfs_v2_1_csv` | `a5ed82b4487aabb053cbb7bf02bb7f528a552cebd3fbd4b11ebd02200b17028a` |
| `hrrr_v2_1_csv` | `43e73fba55922a3e3cbef2cdcf8ed45d3a72bc00ff4494bf1d7d9473a55f45f5` |

Missed checkpoints are never backfilled. A checkpoint with no capture inside its
window gets a `MISSED` receipt from `reconcile_missed()` on the next cycle.

## 1. Components

| Piece | Where | Purpose |
| --- | --- | --- |
| `scripts/weather_prospective_task.sh` + `deploy/systemd/*` | Oracle VM | hourly :05 UTC cycle, `flock`, ownership guard, retry once, post-cycle ping + backup |
| `scripts/weather_prospective_task.ps1` | Windows | same flow; ownership guard only when a collector config exists |
| `research/weather/collector_ops.py` | both | ownership marker, backup, restore, archive, stop-status, verification |
| control repo, ref `refs/heads/ownership` | private GitHub repo `kalshi-edge-lab-control` (to create) | `OWNER.json` ownership marker |
| state repo, ref `refs/heads/state` | private GitHub repo `kalshi-edge-lab-state` (to create) | byte-exact state backup |
| two Healthchecks.io checks | Hobbyist ($0) | `collector` health and `backup` health, alerted separately |

Collector config (`/etc/kalshi-edge-lab/collector.json` on Linux,
`collector.local.json` in the repo root on Windows, never committed); template in
`deploy/collector.example.json`.

## 2. Ownership marker

`OWNER.json` (`schema: kel_collector_owner_v1`) on `refs/heads/ownership` names
the one collector allowed to run. Every collector entry point (systemd runner,
Windows runner, manual `backup`) runs `collector_ops guard` before doing work:

- The marker is fetched fresh from the remote every cycle; nothing is cached.
- Fetch fails, ref missing, or marker malformed: exit 21, cycle skipped,
  `collector` check pinged `/fail`.
- Marker names another collector: exit 20, cycle skipped, alert.
- Linux has no standalone mode: no config means exit 22 and no cycle.
- Windows without a config keeps the pre-migration behaviour (no guard). The
  moment `collector.local.json` exists, enforcement is mandatory there too.

Ownership changes go through `ownership init|transfer`, which:

- compare-and-swaps (`--from` must equal the current owner; `init` fails if a
  marker exists) and pushes without force;
- requires a stop-status JSON from the outgoing host showing
  `confirmed_stopped: true` (less than 6 h old) and unsynced status `none` or
  `not_configured`, or a recorded `--unsynced-blocked-reason`;
- without a stop status, requires `--i-confirm-outgoing-cannot-run` plus both
  `--outgoing-unconfirmed-reason` and `--unsynced-blocked-reason`, all stored in
  the marker. This is a human decision; nothing activates a second host
  automatically.

Access: collectors get a **read-only** deploy key on the control repo. Only the
operator (your own GitHub credentials) can write the marker.

## 3. State backup

Separate from ownership: different repository (recommended) or at least a
different ref; `load_config` rejects the same remote + ref. Backups never read
or write the ownership ref.

- Explicit allowlist (`collector_ops.STATE_DIRS`, `STATE_FILES`,
  `data/logs/prospective/*.log`). Excluded: `cycle.lock`, `cycle.flock`, dotfiles
  and `.cycle_*` temporaries, `*.tmp`, `collector.local.json`, `.env`,
  `data/cache/` (except in the one-time migration archive), credentials.
- Byte-exact: the state repo's `.gitattributes` is `* -text`, every git call
  runs with `core.autocrlf=false core.safecrlf=false`, and `MANIFEST.json`
  stores SHA-256 and size for each file. `verify-backup` re-clones with
  `autocrlf=true eol=crlf` and checks every hash.
- Never force-pushes, merges or rebases. If the remote has commits this host
  lacks, the status is `conflict` and a human resolves it.
- Refuses to mirror if a write-once or append-only file vanished locally or an
  append-only file shrank.

Statuses (in the post-cycle log line and the `backup` check body):

| Status | Meaning | Action |
| --- | --- | --- |
| `pushed` / `up_to_date` | remote has this host's state | none |
| `push_failed` | remote unreachable; local commit kept, `unsynced_commits` > 0 | next cycle retries; investigate if it persists |
| `conflict` | remote history diverged or push rejected | stop and investigate (two writers?) |
| `failed` | refused locally (vanished/shrunk files, missing clone, lock) | investigate |

Collector health and backup health are separate checks. A failed push never
changes the cycle exit code and never blocks a capture. Unsynced state stays on
the collector's disk and in its local backup clone.

## 4. Billable defaults to avoid (Oracle)

Use only resources labelled "Always Free-eligible". During the 30-day trial
the console also offers paid resources; avoid them. Specifically:

- Shape `VM.Standard.E2.1.Micro` only (x86_64). Do not use flexible E3/E4/E5 shapes.
- One boot volume, default size (<= 200 GB total across volumes). No extra block volumes.
- Boot-volume backup policy: **none** (only 5 manual backups are free).
- No load balancer, NAT gateway, database, Object Storage tiers beyond free, or second region.
- Ephemeral public IP (reserved IPs are fine while attached, but unnecessary).
- Set a $1 Budget alert. Do not upgrade to Pay As You Go.

## 5. Setup (after approval; nothing below has been done)

1. Oracle signup (you), US home region, $1 card authorization, no upgrade.
2. VCN wizard with a public subnet; ingress only SSH from your IP.
3. Instance: E2.1.Micro, Ubuntu 24.04 x86 (Always Free-eligible), your SSH key.
   On "out of host capacity", retry other ADs daily; if it never succeeds,
   use section 10.
4. Server: 2 GB swapfile; `timedatectl set-ntp true`; `apt install git`;
   install `uv`; `uv python install 3.14.0`; user `kalshi`.
5. Staging clone at `/srv/kalshi-edge-lab-staging`:
   ```bash
   git clone https://github.com/kylebabington/kalshi-edge-lab.git /srv/kalshi-edge-lab-staging
   cd /srv/kalshi-edge-lab-staging
   sha256sum data/weather/calibration/{gfs,hrrr}_operational_replay_obs_v2_1.csv  # must equal the pins
   uv venv --python 3.14.0 .venv && uv pip install --python .venv/bin/python -r requirements-lock.txt
   .venv/bin/python -m pytest -q
   .venv/bin/python -m research.weather.collector_ops verify-pins
   ```
6. External services (you): private `kalshi-edge-lab-control` and
   `kalshi-edge-lab-state` repos; deploy keys generated on the VM (control:
   read-only; state: write); two Healthchecks checks (period 1 h, grace 20 min).
   Write `/etc/kalshi-edge-lab/collector.json` (mode 600, owner `kalshi`).
7. Install units, **timer disabled**:
   ```bash
   sudo cp deploy/systemd/kalshi-weather-prospective.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload   # do not enable yet
   ```

## 6. Rehearsal (no ownership change, Windows keeps collecting)

On Windows, between cycles (not within :00-:20 and outside the 06/09/12/15/18 ET windows):

```powershell
.\.venv\Scripts\python.exe -m research.weather.collector_ops archive --out $env:TEMP\kel\state.tar --profile migration
# prints archive_sha256; also written to state.tar.json
```

Copy `state.tar` to the VM staging tree, then:

```bash
.venv/bin/python -m research.weather.collector_ops restore --archive state.tar --archive-sha256 <sha> --apply
.venv/bin/python -m research.weather.collector_ops verify-pins
.venv/bin/python -m research.weather.collector_ops reproduce-all
```

Pass criteria: restore `safe: true` with `verification.verified_files` equal to
the archive file count; `verify-pins` `ok: true`; `reproduce-all` `ok: true`, with
`audited_exceptions_seen` exactly `2026-10-03/d0_0600/iem_knyc.csv` and
`2026-10-03/d0_0900/iem_knyc.csv` (`CRLF_NORMALIZED_MATCH_ONLY`, see
`phase7_evidence_line_endings_note.md`). Any other non-`RAW_MATCH` evidence fails.

This rehearsal was run on Windows on 2026-10-07 against the live state (3,163
files, 14 records): all checks passed.

## 7. Handoff (Windows -> Oracle)

Choose an evening between 19:10 and 21:50 ET, before 2026-10-31. Nothing is
captured in that span and the next window opens at the 06:00 ET checkpoint.

1. **Put Windows under the marker first** (days before the handoff, between cycles):
   ```powershell
   python -m research.weather.collector_ops ownership init --remote <control-repo-url> `
       --to windows-laptop --adopt-running-collector --reason "first owner: the running Windows collector"
   ```
   Then copy `deploy/collector.example.json` to `collector.local.json` in the
   Windows repo root with `collector_id: windows-laptop` (marker first, config
   second; the other order makes the next cycle skip with exit 21). Confirm the
   next Windows cycle logs `"ownership": "owner"` and both checks go green.
2. **Stop the outgoing collector** (Windows):
   ```powershell
   Disable-ScheduledTask -TaskName KalshiEdgeLab-WeatherProspective
   # wait for any active cycle to finish (cycle.lock disappears)
   .\.venv\Scripts\python.exe -m research.weather.collector_ops stop-status --out $env:TEMP\kel\stop.json
   ```
   Exit 0 only when the task is `Disabled`, `cycle.lock` is absent and no
   `weather_model.py --prospective-cycle` process exists. If it is not 0, do
   not continue; re-enable the task if you abort.
3. **Final state sync**: `archive` (as in section 6), copy, then on the VM
   restore into a clean clone at `/srv/kalshi-edge-lab`, and run `verify-pins`
   and `reproduce-all`. If the backup is configured on Windows, `unsynced` must
   report `none` (it is part of the stop status).
4. **Transfer ownership** (operator):
   ```powershell
   python -m research.weather.collector_ops ownership transfer --from windows-laptop --to oracle-micro-1 `
       --reason "handoff to Oracle" --stop-status $env:TEMP\kel\stop.json
   ```
5. **Start the backup and the timer** (VM):
   ```bash
   .venv/bin/python -m research.weather.collector_ops backup-init --create
   .venv/bin/python -m research.weather.collector_ops backup
   .venv/bin/python -m research.weather.collector_ops verify-backup
   sudo systemctl enable --now kalshi-weather-prospective.timer
   ```
6. Leave the Windows task registered but **Disabled**, with
   `collector.local.json` in place. If it is ever re-enabled by mistake, the
   guard refuses because the marker names `oracle-micro-1`.

## 8. Verify after handoff

- First :05 UTC run: log line in `data/logs/prospective/<date>.log`,
  `"ownership": "owner"`, cycle exit 0, backup `pushed`, both checks green.
- Next checkpoint: `CAPTURED`, `finalized_within_capture_window: true`, empty
  `integrity_problems`; `reproduce-all` still OK.
- `pending_scores` keep retrying until settlement writes final scores.
- `verify-backup` from your laptop (fresh clone) passes.

## 9. Failover / rollback (any direction, one collector only)

Never start a second host until the first is confirmed stopped or a human has
recorded why it cannot be.

1. Disable the outgoing host's trigger
   (`sudo systemctl disable --now kalshi-weather-prospective.timer`, or
   `Disable-ScheduledTask`), let any active cycle finish, then run `stop-status`
   on it. It must show `confirmed_stopped: true`.
2. Recover unsynced captures: if `stop-status` shows unsynced `unsynced` or
   `blocked`, run `backup` on the outgoing host (it is still the owner) until
   `pushed`, or copy its state with `archive`. If neither is possible, the
   captures are reported as unrecoverable in `--unsynced-blocked-reason`. They
   are never reconstructed from later downloads.
3. On the incoming host, with its trigger still disabled:
   ```
   python -m research.weather.collector_ops restore --from-backup           # plan only
   python -m research.weather.collector_ops restore --from-backup --apply   # only if "safe": true
   python -m research.weather.collector_ops verify-pins
   python -m research.weather.collector_ops reproduce-all
   ```
   The plan refuses (exit 32) if the incoming host holds a write-once capture
   or append-only log entries the backup lacks, or one that differs. Resolve by
   hand; never overwrite captures.
4. `ownership transfer --from <outgoing> --to <incoming> --stop-status stop.json`.
5. Enable the incoming trigger. Rollback to Windows requires its
   `collector.local.json` so the guard stays enforced there.

Outgoing host unreachable (VM terminated, disk lost): its state since the last
`pushed` backup is lost. Run `ownership transfer` with
`--i-confirm-outgoing-cannot-run --outgoing-unconfirmed-reason "..." --unsynced-blocked-reason "..."`
only after confirming in the Oracle console that the instance is stopped or
terminated. If it may come back, the marker already makes it skip.

## 10. If the VM is reclaimed or lost

- Idle reclamation (CPU p95 < 20% and network < 20% over 7 days) stops the VM;
  Oracle normally emails first. Healthchecks alerts within about 80 minutes.
- Checkpoints during the outage are not captured. The next cycle anywhere
  writes `MISSED` receipts (`window_elapsed_without_capture`). No backfill.
- Preferred: restart the instance from the console; the disk persists. When
  the timer fires, the guard re-checks ownership before collecting.
- Otherwise follow section 9 with the unconfirmed path once the console shows
  the instance stopped/terminated.

## 11. Fallback: GitHub Actions (only if no Always Free capacity)

Public repo, so standard runners are free. Scheduled runs are best-effort and
can be delayed or dropped near the top of the hour; use
`cron: '4,12,20 * * * *'`, `timeout-minutes: 20`, and
`concurrency: {group: phase7-collector, cancel-in-progress: false}`. Each run
must restore from the state repo, run `guard` with a config whose
`collector_id` is `github-actions`, run the cycle, then `post`. A failed push
after a capture must fail the job and upload the state as an artifact (90-day
retention). Expect more `MISSED` checkpoints than on a VM.

## 12. Command reference

`python -m research.weather.collector_ops <cmd>`; exit codes: 0 ok, 20 not
owner, 21 ownership unverified, 22 config, 30 backup failed, 32 unsafe/refused,
33 verification failed.

| Command | Use |
| --- | --- |
| `guard [--require-config]` | ownership check (runners) |
| `post --cycle-exit N` | collector ping + backup (runners; never changes the exit code) |
| `ownership show\|init\|transfer` | read or compare-and-swap the marker |
| `stop-status [--out f]` | confirm this host's collector is stopped, with unsynced report |
| `unsynced` | local captures missing from the remote backup |
| `backup-init [--create]`, `backup`, `verify-backup` | state backup |
| `archive --out f [--profile migration\|backup]` | consistent hash-manifested tar |
| `restore --archive f --archive-sha256 h \| --from-backup [--apply]` | verified restore |
| `verify-pins`, `reproduce-all` | protocol/calibration/method hashes; offline reproduction |
