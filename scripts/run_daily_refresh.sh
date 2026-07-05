#!/usr/bin/env bash
# Wrapper for the daily data pipeline refresh (Linux/macOS cron or manual).
# Keeps edge_scores, prices, and catalysts current for paper autopilot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ"

LOGDIR="$PROJ/data/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/daily_refresh_$(date +%F).log"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$PROJ/.venv/bin/python" ]]; then
    PYTHON="$PROJ/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi

{
  echo "==== $(date -Iseconds) : refresh_all start ===="
  "$PYTHON" "$PROJ/scripts/refresh_all.py"
  ec=$?
  echo "==== $(date -Iseconds) : exit=$ec ===="
  exit "$ec"
} >>"$LOG" 2>&1
