# setup-schedule.ps1 — (re)register the weekly refresh task with settings that
# actually survive a laptop being asleep, on battery, or shut down.
#
# Run once, as the normal user (no admin needed for an interactive task):
#   powershell -ExecutionPolicy Bypass -File .\setup-schedule.ps1
#
# Why the old task never fired: the default Task Scheduler settings skip a run
# entirely if the machine is asleep/off at the trigger time, and refuse to start
# on battery power. StartWhenAvailable + battery flags below fix both.
#
# All output is ASCII so it renders correctly in every PowerShell version.

$ErrorActionPreference = "Stop"

$TaskName = "HP OmniBook Weekly Review Refresh"
$RepoDir  = $PSScriptRoot
$BatFile  = Join-Path $RepoDir "run_weekly.bat"

Write-Host ""
Write-Host "=== Weekly refresh schedule setup ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoDir"
Write-Host ""

if (-not (Test-Path $BatFile)) {
    Write-Host "ERROR: run_weekly.bat not found at $BatFile" -ForegroundColor Red
    exit 1
}

# --- Show what is there today -------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[info] Existing task found. Last run result:" -ForegroundColor Yellow
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "       Last run time : $($i.LastRunTime)"
    Write-Host "       Last result   : 0x$('{0:X}' -f $i.LastTaskResult)  (0x0 = success)"
    Write-Host "       Next run time : $($i.NextRunTime)"
    Write-Host "       Replacing it..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host ""
}

# --- Build the task -----------------------------------------------------------
# Runs Mondays 09:00. Change -At if you want a different slot.
$action = New-ScheduledTaskAction -Execute $BatFile -WorkingDirectory $RepoDir

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30)

# Interactive: runs under your logged-on session, so Playwright can open a browser
# and no stored password is required.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Weekly Best Buy US/CA + Walmart review refresh for the OmniBook dashboard. Runs weekly_update.py (scrape -> build -> push)." | Out-Null

Write-Host "[ok] Task registered." -ForegroundColor Green
Write-Host ""

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "  Name      : $TaskName"
Write-Host "  Runs      : Mondays 09:00"
Write-Host "  Next run  : $($info.NextRunTime)"
Write-Host ""
Write-Host "  Key settings now enabled:"
Write-Host "    - Start as soon as possible after a missed run  (was the main failure)"
Write-Host "    - Allow start / keep running on battery"
Write-Host "    - Wake the computer to run"
Write-Host "    - Retry up to 3 times, 30 min apart"
Write-Host ""
Write-Host "NOTE: if the laptop is fully shut down at 09:00 Monday, the run happens"
Write-Host "      the next time you boot - not never, which is what used to happen."
Write-Host ""

# --- Offer an immediate test run ---------------------------------------------
Write-Host "Run it once now to confirm it works end to end? (y/N): " -NoNewline -ForegroundColor Cyan
$answer = Read-Host
if ($answer -eq "y" -or $answer -eq "Y") {
    Write-Host ""
    Write-Host "Starting task... (this takes 10-20 min; it scrapes, builds AND pushes)" -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    $s = (Get-ScheduledTask -TaskName $TaskName).State
    Write-Host "Task state: $s"
    Write-Host ""
    Write-Host "Watch progress in:  $RepoDir\logs\"
    Write-Host "Check the result later with:"
    Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'" -ForegroundColor White
    Write-Host "  (LastTaskResult 0x0 = success)"
} else {
    Write-Host ""
    Write-Host "Skipped. To test manually later:" -ForegroundColor Yellow
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
}
Write-Host ""
