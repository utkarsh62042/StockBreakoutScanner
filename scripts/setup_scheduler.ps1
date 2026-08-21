<#
Registers Windows Task Scheduler jobs for the breakout scanner.

Three weekday tasks (the jobs themselves skip exchange holidays via
breakout.trading_calendar):

    09:30  morning_scan    — build the setup watchlist
    15:00  preclose_scan   — confirm breakouts, emit alerts
    16:00  eod_settle      — settle paper trades, flag failed breakouts

Times are your machine's LOCAL clock — set the machine to IST (or adjust below).

Run once from an elevated PowerShell:
    powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1

Remove them later with:
    powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1 -Remove
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$py = Join-Path $root ".venv\Scripts\python.exe"

$jobs = @(
    @{ Name = "BreakoutScanner_Morning";  Module = "breakout.jobs.morning_scan";  Time = "09:30" },
    @{ Name = "BreakoutScanner_PreClose"; Module = "breakout.jobs.preclose_scan"; Time = "15:00" },
    @{ Name = "BreakoutScanner_EodSettle";Module = "breakout.jobs.eod_settle";    Time = "16:00" }
)

foreach ($job in $jobs) {
    if (Get-ScheduledTask -TaskName $job.Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $job.Name -Confirm:$false
        Write-Host "Removed existing task $($job.Name)"
    }
    if ($Remove) { continue }

    $action  = New-ScheduledTaskAction -Execute $py -Argument "-m $($job.Module)" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $job.Time
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    Register-ScheduledTask -TaskName $job.Name -Action $action -Trigger $trigger `
        -Settings $settings -Description "NIFTY 500 breakout scanner: $($job.Module)" | Out-Null
    Write-Host "Registered $($job.Name) at $($job.Time) (Mon-Fri) -> $($job.Module)"
}

if ($Remove) { Write-Host "All breakout scanner tasks removed." }
else { Write-Host "`nDone. Verify in Task Scheduler. -StartWhenAvailable runs a missed job on next wake." }
