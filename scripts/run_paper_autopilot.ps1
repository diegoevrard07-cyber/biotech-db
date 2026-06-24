# Wrapper for the paper-trading autopilot, called by Windows Task Scheduler.
# Runs one daily cycle (refresh prices -> execute due exits -> open new -> snapshot)
# and appends all output to a dated log so you can see exactly what happened.
$ErrorActionPreference = "Continue"
$proj   = "C:\Users\Diegos PC\Documents\biotech-db"
$python = "C:\Users\Diegos PC\AppData\Local\Programs\Python\Python314\python.exe"
$logdir = Join-Path $proj "data\logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$log = Join-Path $logdir ("autopilot_run_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
Set-Location $proj
"==== $(Get-Date -Format o) : paper_autopilot start ====" | Out-File -Append -Encoding utf8 $log
& $python "scripts\paper_autopilot.py" *>> $log
"==== $(Get-Date -Format o) : exit=$LASTEXITCODE ====" | Out-File -Append -Encoding utf8 $log
