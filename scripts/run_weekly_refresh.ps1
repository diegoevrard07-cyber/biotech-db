# Wrapper for the WEEKLY full pipeline refresh, called by Windows Task Scheduler.
# Runs refresh_all.py (ingest catalysts/prices/positioning/insider -> score ->
# resolve outcomes -> build_event_returns -> action sheet -> verify). This is what
# keeps the opportunity set current AND grows the research datasets over time.
# Fail-soft per stage (refresh_all handles that); we just log everything.
$ErrorActionPreference = "Continue"
$proj   = "C:\Users\Diegos PC\Documents\biotech-db"
$python = "C:\Users\Diegos PC\AppData\Local\Programs\Python\Python314\python.exe"
$logdir = Join-Path $proj "data\logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$log = Join-Path $logdir ("weekly_refresh_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
Set-Location $proj
"==== $(Get-Date -Format o) : weekly refresh start ====" | Out-File -Append -Encoding utf8 $log
& $python "scripts\refresh_all.py" *>> $log
"==== $(Get-Date -Format o) : refresh_all exit=$LASTEXITCODE ====" | Out-File -Append -Encoding utf8 $log
