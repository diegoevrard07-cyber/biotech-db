#!/bin/bash
# Double-click this file in Finder (or run in Terminal) to install paper-trading schedulers.
# Uses launchd — missed jobs run when your Mac wakes.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJ="$(pwd)"

echo "=== Biotech DB scheduler install ==="
echo "Project: $PROJ"
echo ""

if [[ ! -f "$PROJ/.env" ]]; then
  echo "ERROR: .env not found. Copy .env.example and set DATABASE_URL first."
  read -r -p "Press Enter to close..."
  exit 1
fi

chmod +x "$PROJ/scripts/setup_scheduler.sh" \
         "$PROJ/scripts/setup_launchd_macos.sh" \
         "$PROJ/scripts/run_daily_refresh.sh" \
         "$PROJ/scripts/run_paper_autopilot.sh"

"$PROJ/scripts/setup_scheduler.sh" --remove 2>/dev/null || true
"$PROJ/scripts/setup_scheduler.sh"

echo ""
echo "=== Done ==="
launchctl list | grep biotech-db || true
echo ""
echo "Schedule (Europe/Brussels): refresh 23:00, autopilot 23:30 weekdays"
echo "Logs: $PROJ/data/logs/"
read -r -p "Press Enter to close..."
