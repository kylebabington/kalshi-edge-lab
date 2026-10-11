<#
.SYNOPSIS
  Task Scheduler action for the 5-minute sports collector cycle (KalshiEdgeLab-SportsProspective).

.DESCRIPTION
  - Runs this repo's .venv\Scripts\python.exe (absolute path) from the repo root and refuses any
    other interpreter.
  - The cycle itself (python -m research.sports.ops.collector cycle) holds the shared sports lock
    data\logs\sports\cycle.lock, which manual `research.sports.live.run capture` and
    `research.sports.scoring.run settle` also take. A stale lock is removed only after its owner
    process is confirmed gone. The lock is released on failure.
  - Wrapper lines and Python stderr go to data\logs\sports\task_YYYY-MM-DD.log (UTC); the cycle
    writes its own data\logs\sports\collector_YYYY-MM-DD.log, collector_cycles.jsonl and
    collector_status.json.
  - No retry loop: the next cycle starts 5 minutes later and accepted records are never replaced.
  - Independent of the weather task, its wrapper, lock and logs.
#>
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $RepoRoot 'data\logs\sports'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ('task_' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.log')

function Write-Log([string]$Message) {
    $line = '{0} [pid {1}] {2}' -f (Get-Date).ToUniversalTime().ToString('o'), $PID, $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

$exitCode = 1
try {
    Write-Log "task start; repo=$RepoRoot"
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
    $out = Join-Path $LogDir ".task_$stamp.out"
    $err = Join-Path $LogDir ".task_$stamp.err"
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'research.sports.ops.collector', 'cycle', '--purpose', 'scheduled') `
        -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $exitCode = $proc.ExitCode
    if (Test-Path $err) {
        Get-Content -Path $err -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { Write-Log ("  stderr: " + $_) }
        Remove-Item $err -Force
    }
    if (Test-Path $out) { Remove-Item $out -Force }
} catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    $exitCode = 12
} finally {
    Write-Log "task end (exit $exitCode)"
}
exit $exitCode
