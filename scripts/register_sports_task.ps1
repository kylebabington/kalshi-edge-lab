<#
.SYNOPSIS
  Register (or re-register) ONLY the KalshiEdgeLab-SportsProspective task. Never touches the weather task.

.DESCRIPTION
  Every 5 minutes, indefinitely, starting at the next 5-minute boundary. The cycle works in UTC, so the
  host timezone does not matter. Settings: no overlapping instances (IgnoreNew; the Python cycle also
  holds the shared sports lock), start when available after a missed trigger, 30-minute execution
  limit, no restart loop (the next 5-minute trigger is the retry). Same principal as the weather task:
  the current user, Interactive logon (runs only while logged on), Limited run level, no stored
  password. Power settings are not changed and the task does not wake the computer.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_sports_task.ps1
#>
param(
    [string]$TaskName = 'KalshiEdgeLab-SportsProspective'
)

$ErrorActionPreference = 'Stop'
if ($TaskName -like '*Weather*') { throw "refusing to register a weather task from the sports script" }
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $RepoRoot 'scripts\sports_prospective_task.ps1'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $Python)) { throw "repo venv interpreter missing: $Python" }
if (-not (Test-Path $Runner)) { throw "runner missing: $Runner" }

$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`""
$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments -WorkingDirectory $RepoRoot
$now = Get-Date
$start = $now.Date.AddMinutes([math]::Ceiling(($now - $now.Date).TotalMinutes / 5) * 5 + 1)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'kalshi-edge-lab: 5-minute sports collector (sports_phase2_capture_v2 window capture + hourly settlement check). RESEARCH_ONLY / NO_BET.' | Out-Null
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
    RepeatDuration     = $task.Triggers[0].Repetition.Duration
    MultipleInstances  = $task.Settings.MultipleInstances
    StartWhenAvailable = $task.Settings.StartWhenAvailable
    ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
    LogonType          = $task.Principal.LogonType
    NextRunTime        = $info.NextRunTime
    LastRunTime        = $info.LastRunTime
    LastTaskResult     = $info.LastTaskResult
} | Format-List
