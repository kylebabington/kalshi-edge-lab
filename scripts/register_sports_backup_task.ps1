<#
.SYNOPSIS
  Register (or re-register) ONLY the KalshiEdgeLab-SportsBackup task. Never touches the capture task
  (KalshiEdgeLab-SportsProspective) or any weather task.

.DESCRIPTION
  Every 15 minutes, indefinitely, starting at the next quarter hour + 7 minutes (so checks do not start on
  capture boundaries). Each run snapshots at most hourly and otherwise only retries an unpushed commit
  (see scripts\sports_backup_task.ps1). Settings: no overlapping instances (IgnoreNew; the Python run also
  holds data\logs\sports\backup.lock), start when available after a missed trigger, 3-hour execution limit
  (a large upload), no restart loop, task priority 7 (below normal). Same principal as the capture and
  weather tasks: the current user, Interactive logon (runs only while logged on), Limited run level, no
  stored password. Power settings are not changed and the task does not wake the computer.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_sports_backup_task.ps1
#>
param(
    [string]$TaskName = 'KalshiEdgeLab-SportsBackup'
)

$ErrorActionPreference = 'Stop'
if ($TaskName -like '*Weather*') { throw "refusing to register a weather task from the sports backup script" }
if ($TaskName -eq 'KalshiEdgeLab-SportsProspective') { throw "refusing to overwrite the sports capture task" }
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $RepoRoot 'scripts\sports_backup_task.ps1'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $Python)) { throw "repo venv interpreter missing: $Python" }
if (-not (Test-Path $Runner)) { throw "runner missing: $Runner" }

$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`""
$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments -WorkingDirectory $RepoRoot
$now = Get-Date
$start = $now.Date.AddMinutes([math]::Ceiling(($now - $now.Date).TotalMinutes / 15) * 15 + 7)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Priority 7 `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'kalshi-edge-lab: private sports-state backup (hourly snapshot, 15-minute upload retry; separate from capture). RESEARCH_ONLY / NO_BET.' | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName           = $task.TaskName
    State              = $task.State
    Execute            = $task.Actions[0].Execute
    Arguments          = $task.Actions[0].Arguments
    WorkingDirectory   = $task.Actions[0].WorkingDirectory
    TriggerStart       = $task.Triggers[0].StartBoundary
    RepeatInterval     = $task.Triggers[0].Repetition.Interval
    MultipleInstances  = $task.Settings.MultipleInstances
    Priority           = $task.Settings.Priority
    ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
    LogonType          = $task.Principal.LogonType
    NextRunTime        = $info.NextRunTime
} | Format-List
