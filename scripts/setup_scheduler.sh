#!/usr/bin/env bash
# Install schedulers for unattended paper trading.
#
#   ./scripts/setup_scheduler.sh              # install with defaults
#   ./scripts/setup_scheduler.sh --dry-run    # show what would be installed
#   ./scripts/setup_scheduler.sh --remove     # remove biotech-db jobs
#
# macOS: uses launchd (missed jobs run when Mac wakes).
# Linux: uses cron.
#
# Defaults (override via env):
#   SCHED_TZ=Europe/Brussels    local timezone for cron times
#   REFRESH_TIME=23:00           daily data refresh (after US market close)
#   AUTOPILOT_TIME=23:30         weekday paper sync (after refresh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$(uname -s)" == "Darwin" ]]; then
  exec "$SCRIPT_DIR/setup_launchd_macos.sh" "$@"
fi

SCHED_TZ="${SCHED_TZ:-Europe/Brussels}"
REFRESH_TIME="${REFRESH_TIME:-23:00}"
AUTOPILOT_TIME="${AUTOPILOT_TIME:-23:30}"

REFRESH_H="${REFRESH_TIME%%:*}"
REFRESH_M="${REFRESH_TIME##*:}"
AUTO_H="${AUTOPILOT_TIME%%:*}"
AUTO_M="${AUTOPILOT_TIME##*:}"

# Convert local wall-clock times to UTC (CRON_TZ is not portable on all cron builds).
_refresh_utc() {
  date -d "TZ=\"$SCHED_TZ\" ${REFRESH_H}:${REFRESH_M}" -u +"%M %H"
}
_autopilot_utc() {
  date -d "TZ=\"$SCHED_TZ\" ${AUTO_H}:${AUTO_M}" -u +"%M %H"
}
REFRESH_UTC="$(_refresh_utc)"
AUTO_UTC="$(_autopilot_utc)"
REFRESH_M_UTC="${REFRESH_UTC%% *}"
REFRESH_H_UTC="${REFRESH_UTC##* }"
AUTO_M_UTC="${AUTO_UTC%% *}"
AUTO_H_UTC="${AUTO_UTC##* }"

MARKER="# biotech-db paper-trading"
REFRESH_LINE="$REFRESH_M_UTC $REFRESH_H_UTC * * * $PROJ/scripts/run_daily_refresh.sh"
AUTOPILOT_LINE="$AUTO_M_UTC $AUTO_H_UTC * * 1-5 $PROJ/scripts/run_paper_autopilot.sh"

DRY_RUN=0
REMOVE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --remove) REMOVE=1 ;;
  esac
done

chmod +x "$PROJ/scripts/run_daily_refresh.sh" "$PROJ/scripts/run_paper_autopilot.sh"

if [[ ! -f "$PROJ/.env" ]]; then
  echo "ERROR: $PROJ/.env not found. Copy .env.example and set DATABASE_URL first." >&2
  exit 1
fi

if [[ "$REMOVE" -eq 1 ]]; then
  if crontab -l 2>/dev/null | grep -q "$MARKER"; then
    crontab -l 2>/dev/null | grep -v "$MARKER" | grep -v "run_daily_refresh.sh" | grep -v "run_paper_autopilot.sh" | sed '/^$/d' | crontab -
    echo "Removed biotech-db cron entries."
  else
    echo "No biotech-db cron entries found."
  fi
  exit 0
fi

echo "Project:  $PROJ"
echo "Timezone: $SCHED_TZ (stored as UTC in crontab)"
echo "Refresh:  daily at $REFRESH_TIME local -> ${REFRESH_H_UTC}:${REFRESH_M_UTC} UTC"
echo "Autopilot: weekdays at $AUTOPILOT_TIME local -> ${AUTO_H_UTC}:${AUTO_M_UTC} UTC"
echo ""

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Would install:"
  echo "  $REFRESH_LINE $MARKER refresh"
  echo "  $AUTOPILOT_LINE $MARKER autopilot"
  exit 0
fi

EXISTING="$(crontab -l 2>/dev/null || true)"
FILTERED="$(echo "$EXISTING" | grep -v "$MARKER" | grep -v "run_daily_refresh.sh" | grep -v "run_paper_autopilot.sh" || true)"
TMP="$(mktemp)"
{
  if [[ -n "$FILTERED" ]]; then
    echo "$FILTERED"
  fi
  echo "$MARKER refresh"
  echo "$REFRESH_LINE"
  echo "$MARKER autopilot"
  echo "$AUTOPILOT_LINE"
} | sed '/^$/d' >"$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "Cron installed. Verify with: crontab -l"
echo ""
echo "Manual test:"
echo "  $PROJ/scripts/run_daily_refresh.sh"
echo "  $PROJ/scripts/run_paper_autopilot.sh"
echo "  # or dry-run: cd $PROJ && .venv/bin/python scripts/paper_autopilot.py --dry-run"
