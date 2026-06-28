#!/usr/bin/env bash
# Wrapper for paper-trading autopilot (Linux/macOS cron or manual).
# Syncs PAPER portfolio to the capped Action Desk and logs to data/logs/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

LOGDIR="$PROJ/data/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/autopilot_run_$(date +%F).log"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$PROJ/.venv/bin/python" ]]; then
    PYTHON="$PROJ/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi

{
  echo "==== $(date -Iseconds) : paper_autopilot start ===="
  "$PYTHON" "$PROJ/scripts/paper_autopilot.py"
  ec=$?
  echo "==== $(date -Iseconds) : exit=$ec ===="
  exit "$ec"
} >>"$LOG" 2>&1
