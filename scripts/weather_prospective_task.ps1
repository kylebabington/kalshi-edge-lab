<#
.SYNOPSIS
  Task Scheduler action for the hourly prospective cycle (incumbent + Phase 7).

.DESCRIPTION
  - Runs this repo's .venv\Scripts\python.exe (absolute path) and refuses any
    other interpreter.
  - Atomic lock (FileMode.CreateNew) holding owner PID, process start time,
    host and user. A lock is removed only when its owner process is gone (or
    the PID now belongs to a different process); never just because it is old.
  - Logs to data\logs\prospective\YYYY-MM-DD.log (UTC timestamps).
  - Retries the cycle once on a non-zero exit; captures are idempotent and
    receipts/records are write-once, so a retry never overwrites a capture.
  - The cycle itself decides due vs MISSED from UTC against each scheduled
    America/New_York checkpoint, so a late (catch-up) run only reconciles.
  - Ownership: if a collector config exists ($env:KEL_COLLECTOR_CONFIG or
    collector.local.json in the repo root), the ownership marker is fetched
    fresh before the cycle; if it cannot be verified or names another
    collector, the cycle is skipped (alert sent). After the cycle, a health
    ping and the state backup run; they never change the cycle exit code.
    Without a config the runner behaves exactly as before the migration.
#>
param(
    [int]$RetryOnFailure = 1,
    [int]$RetryDelaySeconds = 90
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $RepoRoot 'data\logs\prospective'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.log')
$LockPath = Join-Path $LogDir 'cycle.lock'

function Write-Log([string]$Message) {
    $line = '{0} [pid {1}] {2}' -f (Get-Date).ToUniversalTime().ToString('o'), $PID, $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Get-SelfStartUtc {
    (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
}

function Try-AcquireLock {
    $payload = [ordered]@{
        pid               = $PID
        process_start_utc = Get-SelfStartUtc
        host              = $env:COMPUTERNAME
        user              = $env:USERNAME
        acquired_utc      = (Get-Date).ToUniversalTime().ToString('o')
        command           = 'weather_model.py --prospective-cycle'
    } | ConvertTo-Json -Compress
    try {
        $fs = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    } catch [System.IO.IOException] {
        return $false
    }
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
        $fs.Write($bytes, 0, $bytes.Length)
    } finally {
        $fs.Close()
    }
    return $true
}

function Get-LockState {
    # Returns 'alive', 'dead', or 'unknown' for the current lock owner.
    try {
        $owner = Get-Content -Path $LockPath -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        return @{ state = 'unknown'; owner = $null }
    }
    $proc = Get-Process -Id ([int]$owner.pid) -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return @{ state = 'dead'; owner = $owner } }
    try {
        $start = $proc.StartTime.ToUniversalTime().ToString('o')
    } catch {
        return @{ state = 'alive'; owner = $owner }
    }
    if ($start -ne [string]$owner.process_start_utc) {
        return @{ state = 'dead'; owner = $owner }  # PID reused by another process
    }
    return @{ state = 'alive'; owner = $owner }
}

function Acquire-Lock {
    if (Try-AcquireLock) { return $true }
    $lock = Get-LockState
    if ($lock.state -eq 'alive') {
        Write-Log ("another cycle is running (owner pid {0}, acquired {1}); exiting without work" -f $lock.owner.pid, $lock.owner.acquired_utc)
        return $false
    }
    if ($lock.state -eq 'unknown') {
        $age = (Get-Date) - (Get-Item $LockPath).LastWriteTime
        if ($age.TotalMinutes -lt 5) {
            Write-Log 'lock file unreadable (possibly being written); exiting without work'
            return $false
        }
        Write-Log ("lock file unreadable for {0:N0} min with no identifiable owner; treating as stale" -f $age.TotalMinutes)
    } else {
        Write-Log ("stale lock: owner pid {0} is not running; removing" -f $lock.owner.pid)
    }
    Remove-Item -Path $LockPath -Force -ErrorAction SilentlyContinue
    return (Try-AcquireLock)
}

function Invoke-Cycle {
    return (Invoke-RepoPython @('weather_model.py', '--prospective-cycle'))
}

function Invoke-RepoPython([string[]]$Arguments) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $out = Join-Path $LogDir ".cycle_$stamp.out"
    $err = Join-Path $LogDir ".cycle_$stamp.err"
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $env:COLUMNS = '200'
    $proc = Start-Process -FilePath $Python -ArgumentList $Arguments `
        -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    foreach ($f in @($out, $err)) {
        if (Test-Path $f) {
            Get-Content -Path $f -Encoding UTF8 | ForEach-Object { Write-Log ("  " + $_) }
            Remove-Item $f -Force
        }
    }
    return $proc.ExitCode
}

if (-not (Acquire-Lock)) { exit 0 }
$exitCode = 1
try {
    Write-Log ("cycle start; repo={0}; host_tz={1}" -f $RepoRoot, (Get-TimeZone).Id)
    if (-not (Test-Path $Python)) {
        Write-Log "ERROR: repo venv interpreter missing: $Python"
        exit 10
    }
    $prefix = (& $Python -c "import sys; print(sys.prefix)").Trim()
    $expected = (Resolve-Path (Join-Path $RepoRoot '.venv')).Path
    if ($prefix.TrimEnd('\') -ine $expected.TrimEnd('\')) {
        Write-Log "ERROR: interpreter sys.prefix '$prefix' is not the repo venv '$expected'"
        exit 11
    }
    $etNow = (& $Python -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('America/New_York')).isoformat())").Trim()
    $offsetDiff = ([DateTimeOffset]::Now.Offset - [DateTimeOffset]::Parse($etNow).Offset).TotalMinutes
    Write-Log ("venv ok ({0}); America/New_York now {1}; local-ET offset diff {2} min" -f $prefix, $etNow, $offsetDiff)
    if (($offsetDiff % 60) -ne 0) {
        Write-Log 'WARNING: host offset is not a whole number of hours from ET; :05 local is not :05 ET'
    }

    $collectorConfig = if ($env:KEL_COLLECTOR_CONFIG) { $env:KEL_COLLECTOR_CONFIG } else { Join-Path $RepoRoot 'collector.local.json' }
    $ownershipEnforced = Test-Path -LiteralPath $collectorConfig
    if ($ownershipEnforced) {
        $env:KEL_COLLECTOR_CONFIG = $collectorConfig
        $env:KEL_CYCLE_LOCK_HELD = '1'
        $guardExit = Invoke-RepoPython @('-m', 'research.weather.collector_ops', 'guard', '--require-config')
        if ($guardExit -ne 0) {
            Write-Log "ownership check exit $guardExit; cycle skipped (no capture, no backfill)"
            $exitCode = $guardExit
            exit $exitCode
        }
    } else {
        Write-Log "no collector config at $collectorConfig; pre-migration standalone mode (no ownership check)"
    }

    $exitCode = Invoke-Cycle
    Write-Log "cycle exit code $exitCode"
    $tries = 0
    while ($exitCode -ne 0 -and $tries -lt $RetryOnFailure) {
        $tries++
        Write-Log "retry $tries in $RetryDelaySeconds s (idempotent; existing captures are never overwritten)"
        Start-Sleep -Seconds $RetryDelaySeconds
        $exitCode = Invoke-Cycle
        Write-Log "retry $tries exit code $exitCode"
    }

    if ($ownershipEnforced) {
        [void](Invoke-RepoPython @('-m', 'research.weather.collector_ops', 'post', '--cycle-exit', "$exitCode"))
    }
} catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    $exitCode = 12
} finally {
    $lock = Get-LockState
    if ($lock.owner -and [int]$lock.owner.pid -eq $PID) {
        Remove-Item -Path $LockPath -Force -ErrorAction SilentlyContinue
    }
    Write-Log "cycle end (exit $exitCode)"
}
exit $exitCode
