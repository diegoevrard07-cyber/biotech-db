# Register Windows Task Scheduler jobs for unattended paper trading.
# Run from an elevated PowerShell if tasks fail to register:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
#
# Creates:
#   EdgeEngineRefresh      - daily 18:00  -> refresh_all.py
#   BiotechPaperAutopilot  - weekdays 23:00 -> paper_autopilot.py

$ErrorActionPreference = "Stop"
$proj = Split-Path -Parent $PSScriptRoot
$refreshPs1 = Join-Path $proj "scripts\run_weekly_refresh.ps1"
$autopilotPs1 = Join-Path $proj "scripts\run_paper_autopilot.ps1"

if (-not (Test-Path (Join-Path $proj ".env"))) {
    Write-Error ".env not found in $proj — copy .env.example and set DATABASE_URL first."
}

# Rewrite wrapper paths to this machine's project root + python
$python = $null
if (Test-Path (Join-Path $proj ".venv\Scripts\python.exe")) {
    $python = Join-Path $proj ".venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
} else {
    Write-Error "Python not found. Create .venv or install Python 3.12+."
}

function Update-Wrapper($path, $pyScript) {
    $content = @"
# Auto-updated by setup_scheduler.ps1 on $(Get-Date -Format o)
`$ErrorActionPreference = "Continue"
`$proj   = "$proj"
`$python = "$python"
`$logdir = Join-Path `$proj "data\logs"
New-Item -ItemType Directory -Force -Path `$logdir | Out-Null
`$log = Join-Path `$logdir ("$($pyScript -replace '\.py$','')_run_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
Set-Location `$proj
"==== `$(Get-Date -Format o) : $pyScript start ====" | Out-File -Append -Encoding utf8 `$log
& `$python "scripts\$pyScript" *>> `$log
"==== `$(Get-Date -Format o) : exit=`$LASTEXITCODE ====" | Out-File -Append -Encoding utf8 `$log
"@
    Set-Content -Path $path -Value $content -Encoding utf8
}

Update-Wrapper $refreshPs1 "refresh_all.py"
Update-Wrapper $autopilotPs1 "paper_autopilot.py"

$refreshTask = @{
    TaskName    = "EdgeEngineRefresh"
    Action      = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$refreshPs1`""
    Trigger     = New-ScheduledTaskTrigger -Daily -At "18:00"
    Settings    = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Description = "Biotech DB daily pipeline refresh (edge_scores, prices, catalysts)"
}
$autopilotTask = @{
    TaskName    = "BiotechPaperAutopilot"
    Action      = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$autopilotPs1`""
    Trigger     = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "23:00"
    Settings    = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Description = "Biotech DB paper-trading autopilot (sync PAPER book to action desk)"
}

Register-ScheduledTask @refreshTask -Force | Out-Null
Register-ScheduledTask @autopilotTask -Force | Out-Null

Write-Host "Scheduled tasks registered:"
Write-Host "  EdgeEngineRefresh      daily 18:00  -> $refreshPs1"
Write-Host "  BiotechPaperAutopilot  weekdays 23:00 -> $autopilotPs1"
Write-Host ""
Write-Host "Verify: Get-ScheduledTask EdgeEngineRefresh, BiotechPaperAutopilot"
Write-Host "Test now: powershell -File `"$autopilotPs1`""
