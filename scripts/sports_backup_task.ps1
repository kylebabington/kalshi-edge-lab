<#
.SYNOPSIS
  Task Scheduler action for the private sports-state backup (KalshiEdgeLab-SportsBackup).

.DESCRIPTION
  - Runs this repo's .venv\Scripts\python.exe (absolute path) from the repo root and refuses any
    other interpreter.
  - Every 15 minutes: `research.sports.ops.backup run --if-due --low-priority`. A new snapshot is taken
    at most hourly; a commit that an earlier run could not upload is retried at every check. The sports
    lock is held only for the snapshot listing (seconds); compression, Git and upload run after it is
    released, at below-normal CPU and background I/O priority.
  - Overlap guard: data\logs\sports\backup.lock (a second run exits as skipped_overlap) on top of the
    task's IgnoreNew setting.
  - Wrapper lines and Python output go to data\logs\sports\backup_task_YYYY-MM-DD.log (UTC); the run's
    result is in data\logs\sports\backup_status.json.
  - Independent of the 5-minute capture task and of the weather task and its backup.
#>
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $RepoRoot 'data\logs\sports'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ('backup_task_' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.log')

function Write-Log([string]$Message) {
    $line = '{0} [pid {1}] {2}' -f (Get-Date).ToUniversalTime().ToString('o'), $PID, $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

$exitCode = 1
try {
    Write-Log "backup task start; repo=$RepoRoot"
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
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $out = Join-Path $LogDir ".backup_task_$stamp.out"
    $err = Join-Path $LogDir ".backup_task_$stamp.err"
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'research.sports.ops.backup', 'run', '--if-due', '--purpose', 'scheduled', '--low-priority') `
        -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $exitCode = $proc.ExitCode
    foreach ($f in @($out, $err)) {
        if (Test-Path $f) {
            Get-Content -Path $f -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { Write-Log ("  " + $_) }
            Remove-Item $f -Force
        }
    }
} catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    $exitCode = 12
} finally {
    Write-Log "backup task end (exit $exitCode)"
}
exit $exitCode
