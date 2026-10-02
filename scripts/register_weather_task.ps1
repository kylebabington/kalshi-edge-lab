<#
.SYNOPSIS
  Register (or re-register) ONLY the KalshiEdgeLab-WeatherProspective task.

.DESCRIPTION
  Hourly at :05 local time, indefinitely. Verifies first that local time stays
  a whole number of hours from America/New_York for the next 75 days, so :05
  local is :05 ET (the system timezone is never changed). Settings: no
  overlapping instances (IgnoreNew), start when available after a missed
  trigger or sleep, restart on launch failure, 50-minute execution limit.
  Runs as the current user while logged on (no stored password).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_weather_task.ps1
#>
param(
    [string]$TaskName = 'KalshiEdgeLab-WeatherProspective'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $RepoRoot 'scripts\weather_prospective_task.ps1'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $Python)) { throw "repo venv interpreter missing: $Python" }
if (-not (Test-Path $Runner)) { throw "runner missing: $Runner" }

$local = [TimeZoneInfo]::Local
$ny = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
$t = [DateTime]::UtcNow
$end = $t.AddDays(75)
$identical = $true
while ($t -lt $end) {
    $diff = ($local.GetUtcOffset($t) - $ny.GetUtcOffset($t)).TotalMinutes
    if (($diff % 60) -ne 0) { throw "local timezone '$($local.Id)' is not a whole-hour offset from ET at $t UTC" }
    if ($diff -ne 0) { $identical = $false }
    $t = $t.AddHours(1)
}
Write-Output ("timezone check: local='{0}' identical_to_ET_next_75d={1}" -f $local.Id, $identical)

$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`""
$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments -WorkingDirectory $RepoRoot
$start = (Get-Date).Date.AddMinutes(5)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 50) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'kalshi-edge-lab: hourly prospective weather cycle (incumbent + Phase 7). RESEARCH_ONLY / NO_BET.' | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName          = $task.TaskName
    State             = $task.State
    Execute           = $task.Actions[0].Execute
    Arguments         = $task.Actions[0].Arguments
    WorkingDirectory  = $task.Actions[0].WorkingDirectory
    TriggerStart      = $task.Triggers[0].StartBoundary
    RepeatInterval    = $task.Triggers[0].Repetition.Interval
    RepeatDuration    = $task.Triggers[0].Repetition.Duration
    MultipleInstances = $task.Settings.MultipleInstances
    StartWhenAvailable = $task.Settings.StartWhenAvailable
    RestartCount      = $task.Settings.RestartCount
    ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
    LogonType         = $task.Principal.LogonType
    NextRunTime       = $info.NextRunTime
    LastRunTime       = $info.LastRunTime
    LastTaskResult    = $info.LastTaskResult
} | Format-List
